"""Explicit fixed-peer execution of the candidate harness, with private worker state.

SETUP.workers replaces the single actor only when declared. Each peer runs the
same candidate entrypoint with a different task, namespace, scratch and mailbox.
The dataset supplies prompts/files and a combined verifier, never solver code.
"""
from __future__ import annotations

from eval.ckpt.source_tree import copy_harness, has_bun_runtime
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading

from eval.service.tool_transport import Relay
from eval.verification.verifier import _verify
from data.scaffolds.runtime_extensions import validate_workers, validate_mailbox


def _worker_usage(result):
    """A retained trace prefix cannot establish the worker's final usage total."""
    from eval.observe.tracing import _last_usage
    container = (result.get('runtime_services') or {}).get('container') or {}
    trace_log = (container.get('log_files') or {}).get('trace.jsonl') or {}
    if trace_log.get('truncated'):
        return None
    return _last_usage(result.get('trace', ''))


def run(repo, task, *, setup, verifier, env, timeout_s, verify_timeout_s,
        sandbox, run_id, run_seed, trial_id, recordings_root):
    from eval.run import runner
    from eval.observe.tracing import _materialize, process_outcome
    from eval.policy.runtime_policy import phase_timeout
    from eval.containers import bun_runtime
    workers=setup['workers']
    bun_identity=None
    try:
        validate_workers(workers)
        mailbox = setup.get('mailbox', {'max_messages': 4096, 'max_message_bytes': 65536,
                                       'max_calls': 8192, 'max_line_bytes': 8 * 1024 * 1024})
        validate_mailbox(mailbox)
        if not sandbox or setup.get('tool_relay') or (env or {}).get('kind','files')!='files':
            raise ValueError('workers require sandboxed files without an additional parent tool_relay')
        if (verifier or {}).get('kind')!='command':
            raise ValueError('workers require a combined command verifier')
        for w in workers:
            if w.get('env', {}).get('backend') == 'observed-v1':
                if has_bun_runtime(Path(repo)):
                    raise ValueError('Declared Bun runtimes are not supported by observed workers')
                from eval.containers.observed import validate
                validate(w['env'])
            else:
                refusal=runner._refuse_unobserved_policies(task,w.get('env'),None)
                if refusal is not None:return refusal
                if w.get('env',{}).get('kind','files')!='files':
                    raise ValueError('Docker workers require backend=observed-v1')
        if has_bun_runtime(Path(repo)):
            bun_runtime.admit(Path(repo), os.environ)
            bun_identity=bun_runtime.identity(Path(repo))
    except ValueError as exc:
        return runner._invalid(task,exc,stage='workers admission',service=None)
    root=Path(tempfile.mkdtemp(prefix='hg-workers-'))
    lock=threading.Lock();boxes={w['id']:[] for w in workers};events=[];relays=[]
    outcomes={};cleanup_errors=[]
    result=None
    max_messages=mailbox.get('max_messages')
    max_message_bytes=mailbox.get('max_message_bytes')
    def handler(sender):
        def handle(op,args):
            with lock:
                if op=='receive' and not args:
                    messages=boxes[sender][:];boxes[sender].clear();return messages
                if op!='send' or set(args)!={'to','message'} or args['to'] not in boxes:
                    raise ValueError('worker mailbox accepts send(to,message) and receive()')
                if not isinstance(args['message'],str):
                    raise ValueError('worker message must be text')
                if max_message_bytes is not None and len(args['message'].encode()) > max_message_bytes:
                    raise ValueError('worker message byte limit reached')
                if max_messages is not None and len(events)>=max_messages:
                    raise ValueError('worker message limit reached')
                event={'from':sender,'to':args['to'],'message':args['message'],'sequence':len(events)}
                events.append(event);boxes[args['to']].append(event);return {'sequence':event['sequence']}
        return handle
    try:
        plans=[]
        for w in workers:
            private=root/w['id'];private.mkdir()
            # Only candidate code is copied. Run state and git metadata are not a
            # communication channel between peers. Symlinks remain namespace-confined.
            code=private/'candidate'
            copy_harness(repo,code,symlinks=True,ignore=shutil.ignore_patterns('.git','.state','__pycache__'))
            channel=root/(w['id']+'.sock')
            relay=Relay(channel,handler(w['id']),max_calls=mailbox.get('max_calls'),
                        max_line_bytes=mailbox.get('max_line_bytes')).start();relays.append(relay)
            plans.append((w,private,code,channel))
        def execute(plan):
            w,private,code,channel=plan
            worker_task={'task_id':task['task_id']+'/'+w['id'],'goal':w['goal'],
                         'worker_id':w['id'],'peers':[x['id'] for x in workers]}
            if w.get('env', {}).get('backend') == 'observed-v1':
                from eval.run.container_workers import run as container_worker
                return container_worker(code, worker_task, worker=w, private=private, channel=channel,
                    timeout_s=phase_timeout(timeout_s, (env or {}).get('agent_timeout_s'), w.get('timeout_s')),
                    run_id=run_id, trial_id=trial_id, run_seed=run_seed)
            return runner.run_one(code,worker_task,timeout_s=phase_timeout(timeout_s,(env or {}).get('agent_timeout_s'),w.get('timeout_s')),
                sandbox=True,setup={'files':w.get('files',[])},verifier={'kind':'answer','expected':''},
                env=w.get('env',{'kind':'files'}),run_id=run_id,run_seed=run_seed,
                trial_id=trial_id, _worker={'id':w['id'],'socket':channel,'root':private,
                                          'artifacts':w['artifacts'],
                                          'artifact_max_bytes':w.get('artifact_max_bytes', 16 << 20)})
        with ThreadPoolExecutor(max_workers=len(workers),thread_name_prefix='hg-worker') as pool:
            futures=[pool.submit(execute,p) for p in plans]
            for w, future in zip(workers, futures):
                try:
                    outcomes[w['id']] = future.result()
                except (Exception, SystemExit) as exc:
                    outcomes[w['id']] = runner._invalid(task, exc, stage='worker launch', service=w['id'])
        for relay in relays:relay.stop()
        summary={'schema':'harnessgrad.worker-group.v1','task_id':task['task_id'],
                 'run_id':run_id,'trial_id':trial_id,'events':events,
                 'mailbox_limits': mailbox,
                 'mailbox_transport': {w['id']: dict(r.receipt) for w, r in zip(workers, relays)},
                 'workers':{name:{'exit_code':r.get('exit_code'),'stderr':r.get('stderr'),
                     'usage':_worker_usage(r), 'invalid':r.get('invalid'),
                     'runtime_services': r.get('runtime_services'),
                     'harness_runtime': r.get('harness_runtime'),
                     'artifacts':{p:{k:v for k,v in a.items() if k!='base64'}
                                  for p,a in r.get('artifacts',{}).items()}}
                            for name,r in outcomes.items()}}
        failed={name:r.get('invalid') for name,r in outcomes.items() if r.get('invalid')}
        mismatched=[w['id'] for w in workers
                    if (outcomes.get(w['id'],{}).get('harness_runtime') or {}).get('bun') != bun_identity
                    ] if bun_identity is not None else []
        transport_failed = any(r.receipt.get('refused') or r.receipt.get('errors') for r in relays)
        if failed:
            result=runner._invalid(task,json.dumps(failed),stage='worker execution',service=None)
            result['worker_group'] = summary
        elif transport_failed:
            result=runner._invalid(task,'worker mailbox resource limit or transport failure',
                                   stage='worker mailbox',service=None)
            result['worker_group'] = summary
        elif mismatched:
            result=runner._invalid(task,
                'Workers missing the admitted Bun runtime identity: '+', '.join(mismatched),
                stage='worker runtime identity',service=None)
            result['worker_group'] = summary
        else:
            combined=root/'verify';combined.mkdir()
            _materialize(combined,setup.get('files'),runner.PLATFORM_ROOT)
            for name,r in outcomes.items():
                for path,artifact in r.get('artifacts',{}).items():
                    if artifact['status']=='submitted':
                        target=combined/'workers'/name/path;target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(base64.b64decode(artifact['base64']))
            # This is runtime evidence, separate from actor-submitted patches. Deliver
            # as an immutable projection so a patch cannot forge the status receipt.
            evidence=root/'evidence';evidence.mkdir()
            (evidence/'workers.json').write_text(json.dumps(summary))
            (combined/'worker_record').mkdir()
            spec={**verifier,'inputs':[*(verifier.get('inputs') or []),
                     {'path':'worker_record','from':str(evidence),'immutable':True}]}
            verdict=_verify(combined,spec,'',sandbox=True,env={'kind':'files'},
                            timeout_s=phase_timeout(verify_timeout_s,(env or {}).get('verify_timeout_s')),
                            task_id=task['task_id'],run_id=run_id,trial_id=trial_id)
            # Subprocess/provider failures are explicit; never allow a combined check
            # to turn infrastructure failure into an ordinary zero.
            for name,r in outcomes.items():
                failure=runner._provider_failure(r)
                ending=process_outcome(r.get('exit_code'),r.get('stderr'),wrapper=True)
                if ending in ('stopped_by_signal','relayed_signal','timeout'):
                    failure=failure or ending
                # A worker group requires every peer to finish successfully. A
                # valid-looking leftover patch cannot make a failed peer complete.
                # Keep the legacy single-actor artifact scoring policy unchanged.
                if r.get('exit_code') != 0:
                    failure=failure or f"exit code {r.get('exit_code')}"
                if failure:
                    verdict.update(passed=False,score=None,outcome='unmeasured',
                                   unmeasured=f'worker {name} failed: {failure}')
            usage_records=[summary['workers'][name]['usage'] for name in outcomes]
            trace=json.dumps({'worker_group':summary})+'\n'
            # Providers can report tokens without a total call count. Keep each
            # complete metric independently, without treating missing peers as zero.
            totals={k:sum(u[k] for u in usage_records)
                    for k in ('input_tokens','output_tokens','calls')
                    if usage_records and all(isinstance(u,dict) and
                        type(u.get(k)) is int and u[k]>=0 for u in usage_records)}
            if totals:
                trace=json.dumps({'usage':totals,'source':'worker totals'})+'\n'
            verdict['worker_group']=summary
            result={'task_id':task['task_id'],'answer':'','trace':trace,'exit_code':0,'stderr':'',
                    'verdict':verdict,'worker_group':summary,'env_usage':{},'identity':None}
            if bun_identity is not None:
                result['harness_runtime']={'bun':bun_identity}
        if recordings_root is not None:
            dest=Path(recordings_root);dest.mkdir(parents=True,exist_ok=True)
            archive=Path(tempfile.mkdtemp(prefix='workers-',dir=dest))
            # Preserve a successful peer's submission even if another peer failed
            # before the combined verifier could be launched.
            for name, outcome in outcomes.items():
                for path, artifact in outcome.get('artifacts', {}).items():
                    if artifact['status'] == 'submitted':
                        target = archive / 'workers' / name / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(base64.b64decode(artifact['base64']))
            (archive/'workers.json').write_text(json.dumps(summary))
            result['worker_group']['archive']=str(archive)
        return result
    finally:
        for relay in relays:relay.stop()
        try:shutil.rmtree(root)
        except OSError as exc:cleanup_errors.append(str(exc))
        if result is not None:
            result.setdefault('runtime_services',{})['workers']={
                'worker_group':result['worker_group'],
                'cleanup':{'complete':not root.exists(),'errors':cleanup_errors}}
            if (result.get('harness_runtime') or {}).get('bun') is not None:
                result['runtime_services']['bun']={
                    'identity':bun_identity,'workers':[w['id'] for w in workers]}
