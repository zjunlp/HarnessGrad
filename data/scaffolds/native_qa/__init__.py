"""HarnessGrad transport for seven pinned native upstream QA datasets.

No dataset is loaded at import. Native rows are read only from a digest-bound
source manifest prepared outside the platform; official split membership is
retained. Gold rows never enter task.json or SETUP.
"""
from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re

#: The declared-missing-payload signal (`load()` raises it with the prepare command).
#: Imported here as well as lazily in `missing()` because `getattr` must be able to
#: convert it into `AttributeError` -- see the comment there.
from harnessgrad.config.areas import ConfigMissing
from harnessgrad.paths import DataRootNotConfigured, DatasetPath, configured_path, dataset_root

SOURCE_COMMIT = 'e9c7667945f959d62da27204649a82f94d1b6705'
PACKAGE = Path(__file__).resolve().parent
CATALOG = {
 'searchqa': {'source': 'kyunghyuncho/search_qa', 'revision': '06907e45883b7cae435453b65d598447039fde79', 'config': 'train_test_val', 'splits': ['train','validation','test'], 'metric': 'text_em_f1'},
 'nq': {'source': 'google-research-datasets/natural_questions', 'revision': 'e8103d566bef4154c2c12b17c6095ec5275840cc', 'config': 'default', 'splits': ['train','validation'], 'metric': 'short_answer_text'},
 'bbeh': {'source': 'BBEH/bbeh', 'revision': '08e07a803851822c04399782ece3c4a07ce419f9', 'files': {'test':'bbeh_data.json'}, 'splits': ['test'], 'metric': 'bbeh-correctness'},
 'supergpqa': {'source': 'm-a-p/SuperGPQA', 'revision': '4430d4458112c7d4497fdcf94d7cc223313d6acf', 'files': {'test':'SuperGPQA-all.jsonl'}, 'splits': ['test'], 'metric': 'final_letter_v1'},
 'math': {'source':'EleutherAI/hendrycks_math', 'revision':'21a5633873b6a120296cce3e2df9d5550074f4a3','configs':['algebra','counting_and_probability','geometry','intermediate_algebra','number_theory','prealgebra','precalculus'],'splits':['train','test'],'metric':'math-verify-0.9.0-last-boxed'},
 'math500': {'source':'HuggingFaceH4/MATH-500','revision':'6e4ed1a2a79af7d8630a6b768ec859cb5af4d3be','splits':['test'],'metric':'math-verify-0.9.0-last-boxed'},
 'officeqa_pro': {'source':'databricks/officeqa','revision':'763a8366abf2a3605c381d53586d844dc60fa756','files':{'test':'officeqa_pro.csv'},'splits':['test'],'metric':'officeqa-reward-tolerance-0'},
}


def missing(message):
    from harnessgrad.config.areas import ConfigMissing
    raise ConfigMissing(message)


def bbeh_selection(native_ids, policy='error'):
    """The pinned BBEH policy: exclude every row of each repeated question.

    IDs hash only category/question, never the answer. Counting happens before
    partitioning, so contradictory labels cannot be arbitrarily retained and a
    repeated question cannot leak across the experiment's two sides.
    """
    if policy not in ('error', 'exclude'):
        raise ValueError('BBEH duplicate_questions must be error or exclude')
    counts = Counter(native_ids)
    repeated = {tid for tid, count in counts.items() if count > 1}
    excluded = repeated if policy == 'exclude' else set()
    excluded_rows = sum(counts[tid] for tid in excluded)
    return {'duplicate_questions': policy, 'source_row_count': sum(counts.values()),
        'excluded_duplicate_rows': excluded_rows, 'excluded_question_ids': sorted(excluded),
        'retained_row_count': sum(counts.values()) - excluded_rows}, repeated


def required(row, field):
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'native source requires nonempty {field}')
    return value


def parse_row(name, row, split):
    """Native row -> (native_id, public question, private grade input), or skip NQ."""
    if name == 'searchqa':
        task_id = row.get('id', row.get('key'))
        question = required(row, 'question'); context = row.get('context')
        if context is None and 'search_results' in row:
            results = row['search_results']
            snippets = results.get('snippets') if isinstance(results, dict) else [x.get('snippet') for x in results] if isinstance(results,list) and all(isinstance(x,dict) for x in results) else None
            if not isinstance(snippets,list) or any(s is not None and not isinstance(s,str) for s in snippets):
                raise ValueError('SearchQA malformed snippets')
            context = '\n'.join(s for s in snippets if s is not None)
            if type(task_id) is int: task_id = f'{split}:{task_id}'
            if task_id is None: task_id = hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if not isinstance(context,str) or not isinstance(task_id,str) or not task_id.strip():
            raise ValueError('SearchQA requires context and native id')
        answers = row.get('answers',row.get('answer'))
        answers = [answers] if isinstance(answers,str) else answers
        if not isinstance(answers,(list,tuple)) or not answers or any(not isinstance(a,str) or not a.strip() for a in answers):
            raise ValueError('SearchQA malformed answers')
        return task_id, f'Answer the question using the supplied context. Return only the short answer.\n\nContext:\n{context}\n\nQuestion:\n{question}', {'answers':list(answers),'answer':answers[0]}
    if name == 'nq':
        task_id = required(row,'id'); question = required(row.get('question',{}),'text')
        tokens = row.get('document',{}).get('tokens',{}); words,html = tokens.get('token'),tokens.get('is_html')
        if not isinstance(words,list) or not words or not isinstance(html,list) or len(words)!=len(html) or any(not isinstance(w,str) for w in words) or any(type(f) is not bool for f in html):
            raise ValueError('NQ malformed document tokens')
        annotations = row.get('annotations',{}); shorts,labels=annotations.get('short_answers'),annotations.get('yes_no_answer')
        if not isinstance(shorts,list) or not shorts or not isinstance(labels,list) or len(shorts)!=len(labels):
            raise ValueError('NQ malformed annotations')
        refs=[]
        for short,label in zip(shorts,labels):
            if not isinstance(short,dict) or type(label) is not int or label not in (-1,0,1): raise ValueError('NQ malformed annotation')
            starts,ends=short.get('start_token'),short.get('end_token')
            if not isinstance(starts,list) or not isinstance(ends,list) or len(starts)!=len(ends) or (label!=-1 and starts): raise ValueError('NQ malformed spans')
            spans=[]
            for start,end in zip(starts,ends):
                if type(start) is not int or type(end) is not int or not 0<=start<end<=len(words): raise ValueError('NQ invalid token offsets')
                text=' '.join(w for w,f in zip(words[start:end],html[start:end]) if not f)
                if not text.strip(): raise ValueError('NQ empty answer span')
                spans.append((start,text))
            if label!=-1: refs.append('yes' if label==1 else 'no')
            elif spans: refs.append(' '.join(t for _,t in sorted(spans)))
        context=' '.join(w for w,f in zip(words,html) if not f)
        if not context.strip(): raise ValueError('NQ no document text')
        if not refs: return None
        return task_id, 'Answer the question using the supplied Wikipedia context. Return only the short answer. For multiple answer spans, list all of them in document order separated by spaces. For yes/no questions, return yes or no.\n\nContext:\n'+context+'\n\nQuestion:\n'+question, {'answers':refs,'answer':refs[0]}
    if name == 'bbeh':
        category,question,answer=(required(row,f) for f in ('task','input','target'))
        task_id=hashlib.sha256(json.dumps([category,question],ensure_ascii=False).encode()).hexdigest()
        return task_id,question,{'answer':answer}
    if name == 'supergpqa':
        task_id,question,answer,text=(required(row,f) for f in ('uuid','question','answer_letter','answer'))
        options=row.get('options')
        if not isinstance(options,list) or not 2<=len(options)<=10 or any(not isinstance(s,str) or not s.strip() for s in options): raise ValueError('SuperGPQA malformed options')
        letters='ABCDEFGHIJ'[:len(options)]
        if answer not in letters or len(answer)!=1 or options[ord(answer)-65]!=text: raise ValueError('SuperGPQA answer does not match an option')
        choices='\n'.join(f'{l}. {o}' for l,o in zip(letters,options))
        return task_id,f'{question}\n\n{choices}\n\nChoose the best option. You may reason before answering. End with a line containing only the answer letter (A-{letters[-1]}).',{'answer':answer}
    if name in ('math','math500'):
        problem=required(row,'problem');gold=row.get('answer') or row.get('solution')
        if not isinstance(gold,str) or not gold.strip(): raise ValueError('MATH missing reference')
        from .math_grade import _last_boxed
        boxed=_last_boxed(gold)
        if boxed is not None and not boxed.strip(): raise ValueError('MATH malformed final boxed reference')
        return row.get('unique_id') or hashlib.sha256(problem.encode()).hexdigest(),problem+'\n\nGive your final answer in \\boxed{...}.',{'answer':gold}
    if name == 'officeqa_pro':
        return required(row,'uid'),required(row,'question')+'\n\nSearch the supplied corpus directory. Return your final answer inside <FINAL_ANSWER>...</FINAL_ANSWER>.\n\nCorpus: ./corpus',{'answer':required(row,'answer')}
    raise ValueError('unknown QA dataset')


class _Verifiers(Mapping):
    """Resolve only selected scoring environments, with no process-wide cache."""
    def __init__(self, dataset):
        self.dataset = dataset
        self.rows = {row['task_id']: row for row in dataset.records()}

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, key):
        return self.dataset.verifier(self.rows[key])


class Dataset:
    def __init__(self,name,root=None):
        self.name=name; self.source=CATALOG[name]
        if root is None:
            root=dataset_root(name)
        # Catalogs need only declarations. Resolve and validate storage when
        # records are requested, so imports need neither configuration nor data.
        self.root=root if isinstance(root,DatasetPath) else Path(root).expanduser()
        self._records=None

    def records(self):
        if self._records is not None: return self._records
        root=Path(self.root).resolve()
        if root == PACKAGE.parents[2] or PACKAGE.parents[2] in root.parents:
            raise ValueError('dataset root must be outside the platform tree')
        self.root=root
        path=self.root/'source-manifest.json'
        if not path.is_file(): missing(f'{self.name}: source manifest missing; run python3 -m data.scaffolds.native_qa.prepare --dataset {self.name} --root {self.root}')
        manifest=json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('dataset')!=self.name or manifest.get('source')!=self.source['source'] or manifest.get('revision')!=self.source['revision']:
            raise ValueError(f'{self.name}: source manifest dataset/revision mismatch')
        files=manifest.get('files')
        if not isinstance(files,list) or not files: raise ValueError('source manifest has no files')
        records=[];seen=set();splits=set()
        for item in files:
            rel=item.get('path');split=item.get('split')
            if not isinstance(rel,str) or Path(rel).is_absolute() or '..' in Path(rel).parts or split not in self.source['splits']: raise ValueError('source manifest unsafe path/split')
            source=self.root/rel
            if source.is_symlink() or not source.resolve().is_relative_to(self.root): raise ValueError('source input leaves data root')
            if not source.is_file(): missing(f'{self.name}: missing prepared source file {source}')
            raw=source.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=item.get('sha256'): raise ValueError('source file checksum mismatch')
            rows=[json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
            if len(rows)!=item.get('rows'): raise ValueError('source row count mismatch')
            splits.add(split)
            for row in rows:
                result=parse_row(self.name,row,split)
                if result is None: continue
                native_id,question,gold=result
                native_key=native_id if self.name in ('bbeh','supergpqa','math','math500') else f'{split}/{native_id}'
                if native_key in seen and self.name != 'bbeh': raise ValueError(f'{self.name}: duplicate native question/id; no arbitrary exclusion')
                seen.add(native_key)
                # Native IDs can contain separators. An encoded stable hash retains
                # provenance without allowing path-shaped IDs to escape run storage.
                task_id=f'{self.name}/{split}/'+hashlib.sha256(native_id.encode()).hexdigest()
                records.append({'task_id':task_id,'source_split':split,'native_id':native_id,'question':question,'gold':gold})
        if splits!=set(self.source['splits']): raise ValueError('prepared source omits a native split; partial pool cannot masquerade as full release')
        selection = manifest.get('selection')
        if self.name == 'bbeh':
            if selection is not None and not isinstance(selection, dict):
                raise ValueError('BBEH selection receipt must be an object')
            if selection is not None and any(type(selection.get(key)) is not int for key in
                    ('source_row_count', 'excluded_duplicate_rows', 'retained_row_count')):
                raise ValueError('BBEH selection receipt row counts must be integers')
            policy = selection.get('duplicate_questions') if selection is not None else 'error'
            expected, repeated = bbeh_selection([r['native_id'] for r in records], policy)
            if selection is not None and selection != expected:
                raise ValueError('BBEH selection receipt differs from the digest-bound source rows')
            if repeated and policy == 'error':
                raise ValueError('bbeh: duplicate native question/id; no arbitrary exclusion. '
                    'Prepare with --duplicate-questions exclude to explicitly remove every repeated group')
            if policy == 'exclude':
                records = [r for r in records if r['native_id'] not in repeated]
        elif selection is not None:
            raise ValueError('duplicate-question selection is supported only for BBEH')
        if not records: raise ValueError('no eligible source tasks')
        self._records=records
        return records

    def load(self):
        records=self.records()
        if self.name=='officeqa_pro': self.corpus()
        tasks=[{'task_id':r['task_id'],'goal':r['question']+'\n\nWrite the final answer to answer.txt in your working directory.','inputs':[]}for r in self.records()]
        return tasks,{r['task_id']:'' for r in self.records()}

    def corpus(self):
        corpus=Path(configured_path('HG_DATA_OFFICEQA_PRO_CORPUS',self.root/'corpus')).resolve()
        if not corpus.is_dir(): missing('officeqa_pro requires a separately prepared public corpus directory; question/answer CSV must not be inside it')
        paths=list(corpus.rglob('*'))
        if not paths or any(p.is_symlink() or (p.is_file() and p.suffix.lower() not in ('.txt','.md','.pdf','.json')) for p in paths): raise ValueError('OfficeQA corpus must contain only ordinary public txt/md/pdf/json documents')
        if not any(p.is_file() for p in paths): raise ValueError('empty OfficeQA public corpus')
        return corpus

    def setup_map(self):
        qa=PACKAGE.parent/'qa'
        files=[{'path':'qa/bin/qa','from':str(qa/'cli/qa')},
               {'path':'qa/prediction.py','from':str(qa/'prediction.py')},
               {'path':'qa/prediction.schema.json','from':str(qa/'prediction.schema.json')}]
        if self.name=='officeqa_pro': files.append({'path':'corpus','from':str(self.corpus())})
        return {r['task_id']:{'files':list(files)}for r in self.records()}

    #: The verifier's own package: `check.py` plus the modules it imports, and nothing
    #: else. `__init__.py` is deliberately **not** copied: it imports the platform package
    #: (`harnessgrad.config.areas`) at module level, and this directory is materialised
    #: inside the sandbox, where the platform tree is hidden -- the verifier died with
    #: `ModuleNotFoundError: No module named 'harnessgrad'` because `from native_qa.scoring
    #: import score` executes the package `__init__`. The check creates its own package
    #: with an empty search path and imports only the restored sources listed below.
    #: `prepare.py` and `control.py` are not copied either: the verifier never calls them,
    #: and every file in its directory is code that can affect a score.
    VERIFIER_MODULES = ('check.py', 'scoring.py', 'metrics.py', 'bbeh_official.py',
                        'office_reward.py', 'math_grade.py')

    def verifier(self,r):
        gold=json.dumps(r['gold'],ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(gold.encode()).hexdigest()
        inputs=[{'path':'gold.json','content':gold}]
        for module in self.VERIFIER_MODULES:
            source=PACKAGE/module
            if not source.is_file(): missing(f'{self.name}: verifier module {module} is missing')
            inputs.append({'path':f'verifier/native_qa/{module}','from':str(source)})
        spec = {'kind':'command','argv':['python3','-I','verifier/native_qa/check.py','gold.json',digest,r['task_id'],self.name],
                'inputs':inputs,'scorer_input':{'task_id':r['task_id'],'path':'gold.json','sha256':digest},
                'reward_file':'/verifier/reward.txt','evidence_file':'/verifier/evidence.json',
                'timeout_s':30,'result_contract':{'version':1,'score_range':[0,1],'require_exit_zero':True}}
        if self.name in ('math','math500'):
            from harnessgrad.dataset_runtime import bind, resolve_execution
            binding = bind(resolve_execution('math'), 'verifier')
            spec['argv'][:2] = [binding['python'], '-I', '-B']
            spec['python_runtime'] = binding
            spec['runtime_paths'] = list(binding['runtime_paths'])
        return spec

    def getattr(self,name):
        """One adapter's declared attributes, for the module-level `__getattr__`.

        Every branch below that reads `records()` needs the prepared payload, and the
        payload may be absent. A missing payload is **`AttributeError` here, not
        `ConfigMissing`**: this is `__getattr__`, so raising anything else breaks the
        protocol -- `hasattr()`, `getattr(module, name, default)` and any tool that walks
        the declarations without touching data (e.g. `tools/split_inventory.py --check`,
        which is deliberately payload-free) explode instead of reading "not computable
        here". `load()` still raises `ConfigMissing` with the prepare command, because a
        caller that asked for tasks has asked for data.
        """
        if name=='DEFAULT_ENV': return {'kind':'files'}
        if name=='SPLIT_SOURCE': return 'declared' if 'train' in self.source['splits'] else 'none'
        if name=='_LOAD_ERROR': return None
        try:
            if name=='_RECORDS': return self.records()
            if name=='SETUP': return self.setup_map()
            if name=='VERIFY':
                if self.name in ('math', 'math500'):
                    return _Verifiers(self)
                return {r['task_id']:self.verifier(r)for r in self.records()}
            if name=='ENV': return {r['task_id']:{'kind':'files'}for r in self.records()}
            if name=='SPLIT':
                if 'train' not in self.source['splits']: return None
                return {'train':[r['task_id']for r in self.records()if r['source_split']=='train'],
                        'eval':[r['task_id']for r in self.records()if r['source_split']!='train']}
            if name=='OFFICIAL_EVAL':
                if 'train' not in self.source['splits']: return None
                return [r['task_id']for r in self.records()if r['source_split']!='train']
        except (ConfigMissing, DataRootNotConfigured) as exc:
            raise AttributeError(str(exc)) from exc
        raise AttributeError(name)

    def downloads(self):
        entries=[]
        for split,filename in self.source.get('files',{}).items():
            entries.append({'kind':'hf-file','name':self.name+' native '+split,'repo':self.source['source'],'file':filename,'version':self.source['revision'],'dst':'source/'+filename,'size':'native release file; see HuggingFace manifest','note':'Native source only. Run the pinned prepare command to create digest-bound JSONL; no implicit data download on load.'})
        if not entries: entries=[{'kind':'hf-dataset','name':self.name+' native records','repo':self.source['source'],'version':self.source['revision'],'dst':'source','size':'native table release','note':'The prepare CLI streams the pinned native dataset into JSONL with original source split membership. No data is downloaded on import.'}]
        if self.name in ('math','math500'): entries.append({'kind':'pip','name':'Math-Verify trusted scorer','package':'math-verify==0.9.0','version':'0.9.0','size':'Python scorer dependencies','note':'Exact native mathematical equivalence scorer is mandatory; install separately for the verifier interpreter.'})
        return entries
