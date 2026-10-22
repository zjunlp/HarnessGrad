"""Check this text-only evidence packet; record actual checker/diff outputs and file sizes."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPORT = ROOT / 'registry/w-spreadrt-runtime.md'
PROPOSAL = ROOT / 'registry/proposals/w-spreadrt.json'
VALIDATION = ROOT / 'registry/w-spreadrt-validation.txt'
errors = []
citations = 0
for file in (REPORT, PROPOSAL):
    for path, start, end in re.findall(
            r'((?:data|tests|registry|eval)/[A-Za-z0-9_./-]+):(\d+)(?:-(\d+))?', file.read_text()):
        p = ROOT / path
        citations += 1
        if not p.is_file() or int(end or start) > len(p.read_text().splitlines()):
            errors.append([str(file.relative_to(ROOT)), path, start, end])
assert not errors, errors
proposal = json.loads(PROPOSAL.read_text())
assert proposal['engine_outcome'] == 'found' and proposal['synthetic_control'] == 'failed'
assert proposal['infrastructure_failure_as_zero'] == 'yes'
assert proposal['remaining_gaps']
inventory = json.loads((HERE / 'dependency-inventory.json').read_text())
for record in inventory['source']:
    assert hashlib.sha256(Path(record['path']).read_bytes()).hexdigest() == record['sha256']
for p in HERE.glob('*.py'):
    ast.parse(p.read_text())

commands = [
 ('export TMPDIR=/mnt/20t/xubuqiang/hg-work/spreadrt/tmp; export PYTHONDONTWRITEBYTECODE=1; '
  'python3 tests/fixtures/hg_spreadrt/inventory.py > tests/fixtures/hg_spreadrt/inventory-output.txt 2>&1',
  'inventory-output.txt', 0),
 ('Same inventory command before egg-info correction; output preserved by mv inventory-output.txt inventory-initial-output.txt',
  'inventory-initial-output.txt', 1),
 ('export TMPDIR=/mnt/20t/xubuqiang/hg-work/spreadrt/tmp; export PYTHONDONTWRITEBYTECODE=1; '
  'python3 tests/fixtures/hg_spreadrt/summarize.py > tests/fixtures/hg_spreadrt/summary-output.txt 2>&1',
  'summary-output.txt', 0),
 ('export TMPDIR=/mnt/20t/xubuqiang/hg-work/spreadrt/tmp; export PYTHONDONTWRITEBYTECODE=1; '
  'python3 tests/fixtures/hg_spreadrt/scorer_custody.py > tests/fixtures/hg_spreadrt/scorer-custody-output.txt 2>&1',
  'scorer-custody-output.txt', 0),
 ('export TMPDIR=/mnt/20t/xubuqiang/hg-work/spreadrt/tmp; export PYTHONDONTWRITEBYTECODE=1; '
  'python3 tests/fixtures/hg_spreadrt/run_synthetic.py > tests/fixtures/hg_spreadrt/synthetic-launch-output.txt 2>&1',
  'synthetic-launch-output.txt', 1),
 ('Same launch command before scratch-backed /tmp bind and absent-reward reporting; '
  'outputs preserved by mv synthetic-output.txt synthetic-initial-output.txt and '
  'mv synthetic-launch-output.txt synthetic-initial-launch-output.txt',
  'synthetic-initial-launch-output.txt', 1),
 ('export TMPDIR=/mnt/20t/xubuqiang/hg-work/spreadrt/tmp; export PYTHONDONTWRITEBYTECODE=1; '
  '/usr/bin/bwrap --unshare-net --ro-bind / / --bind /mnt/20t/xubuqiang/hg-work/spreadrt '
  '/mnt/20t/xubuqiang/hg-work/spreadrt --bind /mnt/20t/xubuqiang/hg-work/spreadrt/tmp /tmp '
  '--proc /proc -- python3 tests/fixtures/hg_spreadrt/reward_loss_control.py '
  '> tests/fixtures/hg_spreadrt/reward-loss-output.txt 2>&1', 'reward-loss-output.txt', 0),
 ('Same reward-loss launch before adding selected task_id to _verify; output preserved by '
  'mv reward-loss-output.txt reward-loss-initial-output.txt', 'reward-loss-initial-output.txt', 1),
 ('export TMPDIR=/mnt/20t/xubuqiang/hg-work/spreadrt/tmp; export PYTHONDONTWRITEBYTECODE=1; '
  'python3 -m pytest -q tests/quality/test_spreadsheetbench_classification_w_spread2.py '
  '--basetemp=/mnt/20t/xubuqiang/hg-work/spreadrt/pytest-classification '
  '> tests/fixtures/hg_spreadrt/classification-tests.txt 2>&1', 'classification-tests.txt', 1),
 ('export TMPDIR=/mnt/20t/xubuqiang/hg-work/spreadrt/tmp; export PYTHONDONTWRITEBYTECODE=1; '
  'python3 -m pytest -q tests/quality/test_spreadsheetbench_classification_w_spread2.py '
  '-k test_checker_classifies_failures_and_completed_comparisons '
  '--basetemp=/mnt/20t/xubuqiang/hg-work/spreadrt/pytest-checker-only '
  '> tests/fixtures/hg_spreadrt/checker-only-tests.txt 2>&1', 'checker-only-tests.txt', 0),
 ('rg -n \'^(import |from )|subprocess|soffice|libreoffice|uno|java|https?://\' '
  'data/datasets/spreadsheetbench.py data/datasets/spreadsheetbench/verify/check.py '
  'data/scaffolds/scorer_input.py > tests/fixtures/hg_spreadrt/source-requirements-scan.txt',
  'source-requirements-scan.txt', 0),
]
parts = [
 'w-spreadrt validation: worker evidence only; branch wg-spreadrt.',
 'Worktree: /mnt/20t/xubuqiang/hg-wt/w-spreadrt',
 'Scratch/TMPDIR: /mnt/20t/xubuqiang/hg-work/spreadrt/tmp (outside / and platform tree).',
 'Exact evidence-generation invocations and exact observed outputs follow. '
 'Internal subprocess argv/output are also embedded verbatim in referenced JSON/text receipts.',
 'Initial failed executions are retained separately; no historical receipt/run ID was reused.',
 'Initial packet citation validation command: python3 tests/fixtures/hg_spreadrt/validate_packet.py; exit 1. '
 'Observed final exception: AssertionError: [[\'registry/w-spreadrt-runtime.md\', '
 '\'tests/fixtures/hg_spreadrt/checker-only-tests.txt\', \'1\', \'14\']]. '
 'Read-only wc -l confirmed 12 lines; the report citation endpoint was corrected to 12.',
 'Read-only exploration: pwd; cat AGENTS.md; git status --short --branch; git worktree list --porcelain; '
 'nl -ba source files; rg source/fixture filenames. No main checkout content was modified.',
 'Initial pwd output: /mnt/20t/xubuqiang/hg-wt/w-spreadrt; git status output: ## wg-spreadrt',
 '',
]
for cmd, file, code in commands:
    parts += ['COMMAND: ' + cmd, 'OBSERVED EXIT: ' + str(code),
              'EXACT OUTPUT (' + file + '):', (HERE / file).read_text()]
parts += ['Exact engine-search heredoc executed:', '''export TMPDIR=/mnt/20t/xubuqiang/hg-work/spreadrt/tmp; export PYTHONDONTWRITEBYTECODE=1; python3 - <<'PY' > tests/fixtures/hg_spreadrt/engine-archive-search.json
import json,os
from pathlib import Path
x=json.load(open('tests/fixtures/hg_spreadrt/dependency-inventory.json'))
out={'command':'Python os.walk over each enumerated scope; followlinks=False; match listed filename prefixes and archive suffixes','scopes':x['search']['scopes'],'prefixes':['libreoffice','libuno','uno-libs-private','ure-','ure_','python3-uno','LibreOffice'],'suffixes':['.deb','.rpm','.tar.gz','.tar.xz','.tar.bz2','.conda','.whl'],'matches':[],'errors':[]}
for root in out['scopes']:
 if not Path(root).exists(): out['errors'].append({'scope':root,'error':'root does not exist'}); continue
 def err(e): out['errors'].append({'scope':root,'error':str(e)})
 for base,dirs,files in os.walk(root,followlinks=False,onerror=err):
  for f in files:
   if f.startswith(tuple(out['prefixes'])) and f.endswith(tuple(out['suffixes'])): out['matches'].append(str(Path(base)/f))
print(json.dumps(out,indent=2,sort_keys=True))
PY''', 'OBSERVED EXIT: 0', 'EXACT OUTPUT:', (HERE / 'engine-archive-search.json').read_text(),
 'Additional explicit import/version/path probes: exact subprocess commands, exits, stdout, stderr '
 'and artifact digests:', (HERE / 'additional-probes.txt').read_text(),
 'Exact import probe body: import importlib,importlib.metadata as md,hashlib; '
 'for each name in (pycparser,six,setuptools), import_module(name), version(name), '
 'SHA256(open(module.__file__, rb).read()). No install.',
 (HERE / 'python-dependency-path-probes.txt').read_text(),
 'Stale cache inspection: python3 heredoc, load_workbook(data_only=False), '
 'load_workbook(data_only=True) of the exact authored path below; ZipFile.read '
 'xl/worksheets/sheet1.xml and xl/workbook.xml. Exact output:',
 (HERE / 'stale-cache-inspection.txt').read_text(),
 'Full synthetic exact commands/stdout/stderr/exec trace:',
 (HERE / 'synthetic-initial-output.txt').read_text(), (HERE / 'synthetic-output.txt').read_text(),
 'No direct conversion stdout/stderr capture: checker discards successful engine streams; UNKNOWN.',
 '', 'COMMAND: python3 tests/fixtures/hg_spreadrt/validate_packet.py',
 'citation_locations_checked=' + str(citations), 'citation_errors=[]',
 'proposal_schema_and_claim_states=PASS', 'captured_source_hashes_unchanged=PASS',
 'fixture_python_AST_parse=PASS']
for argv in (['python3', 'tools/check_dataset_blockers.py'], ['git', 'diff', '--check'],
             ['git', 'diff', '--cached', '--check'],
             ['git', '-c', 'core.whitespace=-blank-at-eol,-blank-at-eof',
              'diff', '--cached', '--check'],
             ['git', 'diff', '--name-only', 'HEAD', '--', 'registry/dataset-blockers.json',
              'docs/adapter-integration-journal.md', 'data', 'eval', 'tests/quality'],
             ['git', 'check-ignore', '--no-index', '-v', 'tests/fixtures/hg_spreadrt/inventory.py']):
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    parts += ['COMMAND: ' + json.dumps(argv), 'EXIT=' + str(p.returncode),
              'STDOUT=' + repr(p.stdout), 'STDERR=' + repr(p.stderr)]
    if argv == ['git', 'diff', '--cached', '--check'] and p.returncode:
        # Verbatim nl output contains tabs on blank source lines; trace captures
        # preserve their terminal blank line. Keep exact evidence bytes.
        assert p.returncode == 2
        allowed = {'tests/fixtures/hg_spreadrt/installed-source-excerpts.txt',
                   'tests/fixtures/hg_spreadrt/synthetic-initial-output.txt',
                   'tests/fixtures/hg_spreadrt/synthetic-output.txt'}
        paths = set(re.findall(r'^(tests/[^:]+):\d+:', p.stdout, re.M))
        assert paths <= allowed, paths
        parts += ['Raw diff check reports only literal evidence whitespace: nl-numbered '
                  'blank lines and terminal blank lines from preserved exec traces. '
                  'Evidence bytes remain exact; the subsequent whitespace-configured diff check passes.']
    else:
        assert p.returncode == 0
parts += [
 'Registry checker cannot be affected by these new standalone files: registry and journal unchanged; '
 'the checker above still reports valid=true. Proposal is not integrated.',
 'Explicit NOT VERIFIED:',
 '- No dataset rows (including 283-32), released/gold/init workbook content, full eligible metadata, '
 'task execution, dataset diagnostic, pilot, or coverage was read/run by this packet.',
 '- No model endpoint/server, GPU, API credit, download, install, public artifact availability or '
 'upstream network authentication was used/verified.',
 '- No official upstream evaluator execution; local primitive source comparison and authored local '
 'checker execution do not establish official selected measurement.',
 '- No original LibreOffice package archive digest, full conditional native/plugin/font graph, '
 'archive-to-installed-tree equality, offline reinstall or production read-only sandbox compatibility.',
 '- No general formula freshness/equivalence: stale-cache synthetic control FAILED; underlying '
 'cause/corrective runtime configuration is UNKNOWN.',
 '- No integration-suite pass or repaired test/code claim: 21 cross-layer cases failed at '
 'missing-selected-task-ID admission; 21 direct checker cases passed.',
 '- No production/cache run was used to demonstrate reward-loss persistence; authored verifier '
 'fault injection reproduced score=0 with no unmeasured, and runner consequence is source-derived.',
 '- No absent claim in permission-denied /opt/containerd or three ephemeral wheel caches; '
 'those scopes remain UNKNOWN. No system-wide archive absence claim.',
 '- No frozen/historical receipt updated, old run ID resumed, registry/journal edit, main checkout '
 'commit/merge/push, branch/worktree deletion, or parent merge performed.',
 '', 'File sizes of every file proposed for this one commit (UTF-8 text only; each <1,000,000 bytes):',
]
files = sorted([p for p in HERE.iterdir() if p.is_file()] + [REPORT, PROPOSAL, VALIDATION])
base = '\n'.join(parts) + '\n'
size = 0
while True:
    listing = ''.join(f'{str(p.relative_to(ROOT))}\t{size if p == VALIDATION else p.stat().st_size}\n' for p in files)
    content = base + listing
    current = len(content.encode())
    if current == size:
        break
    size = current
VALIDATION.write_text(content)
for p in files:
    p.read_text()
    assert p.stat().st_size < 1000000
print('packet_validation=PASS')
print('citation_locations_checked=' + str(citations))
print('text_files=' + str(len(files)))
print('max_file_bytes=' + str(max(p.stat().st_size for p in files)))
print('validation_file_bytes=' + str(VALIDATION.stat().st_size))
