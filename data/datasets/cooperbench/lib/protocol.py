"""Pinned source identities and explicit filesystem-only Harbor adaptations."""
from pathlib import Path
import hashlib

SOURCE_REVISION = '63b9d44d9f39a02fccf5bf0052db48a917a011fd'
SOURCE_SHA256 = 'f2af4c7d721987e008e01a03a1f69500a448f7c273cc2bd91e6e093f09c6a8e6'
HARBOR_REVISION = '3e9f14a7c1033896cc600d27de69d6948bcb3410'
HARBOR_ADAPTER_SHA256 = 'dc6adef8c5a04babc86573587c4a7538d89ef3497432a399cb5b464b4ca079fc'
HARBOR_SCORER_SHA256 = '4c410f101b130a35df3a59c376a8972026ee5ba4031a704639a5149eebbd60b2'
METRIC = 'cooperbench.harbor.merged_pair_success.v1'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_checked(path, expected):
    data = Path(path).read_bytes()
    if sha(data) != expected:
        raise ValueError(f'CooperBench source digest mismatch: {path}')
    return data.decode()


def adapt_scorer(source):
    """Keep Harbor merge/filter/test code; move writable mounts into the quota."""
    if sha(source.encode()) != HARBOR_SCORER_SHA256:
        raise ValueError('Harbor scorer differs from the pinned source')
    # Match reviewed mount paths, never path fragments inside the official
    # Python diff filter or the per-agent relative logs directory.
    for old, new in [('/logs/verifier', '/workspace/logs/verifier'),
                     ('/logs/agent/', '/workspace/logs/agent/'),
                     ('/shared/', '/workspace/shared/'), ('/patches', '/workspace/patches'),
                     ('/tests/tests1.patch', '/workspace/private/tests/tests1.patch'),
                     ('/tests/tests2.patch', '/workspace/private/tests/tests2.patch'),
                     ('/usr/local/bin/runner.sh', '/workspace/private/runner.sh')]:
        source = source.replace(old, new)
    return source



def recipe(group):
    import json
    return json.loads((Path(__file__).resolve().parents[1] / 'recipes.json').read_text())[group]


def prepare_recipe(source, group):
    """Return exact pinned dependency block and an offline measurement runner.

    Install ranges are source metadata, not an inferred removal heuristic. Every
    original command outside those ranges survives, including Rust compilation.
    Editable native projects are reinstalled after applying the candidate patch.
    """
    import re
    spec = recipe(group)
    if sha(source.encode()) != spec['runner_sha256']:
        raise ValueError('runner differs from the reviewed source recipe')
    block = ''
    adapted = source
    if spec['install_lines']:
        start, end = spec['install_lines']
        lines = source.splitlines(keepends=True)
        block = ''.join(lines[start:end])
        replacement = block
        replacement = replacement.replace('pip install --upgrade pip', 'true # keep pinned pip')
        replacement = re.sub(r'uv sync[^\n]*', 'true # uv lock resolved during preparation', replacement)
        replacement = re.sub(r'(?:uv pip uninstall --system|pip uninstall)[^\n]*',
                             'true # exact final dependency version is preinstalled', replacement)
        replacement = replacement.replace('uv pip install --system', 'pip install')
        replacement = replacement.replace('pip install', 'python -m pip install --no-index --no-deps --no-build-isolation')
        replacement = ('python3 -m venv --system-site-packages --without-pip /workspace/.cooperbench-venv\n'
                       'export PATH="/workspace/.cooperbench-venv/bin:$PATH"\n' + replacement)
        adapted = ''.join(lines[:start]) + replacement + ''.join(lines[end:])
    if spec['family'] == 'node':
        # Preparation installs the exact pnpm major into the image; avoid npx's
        # network-enabled package resolver. The test command/arguments are intact.
        adapted = adapted.replace('npx pnpm@8 test', 'pnpm test')
    if group == 'dspy_task/task8587':
        # The local LiteLLM fixture needs one asynchronous server process.
        # Its CLI otherwise starts multiple workers which duplicate imports
        # under the task's CPU allocation. Use LiteLLM's native configuration;
        # retain the fixture's readiness check and every official test command.
        adapted = adapted.replace('\nset -e\n', '\nset -e\n\nexport NUM_WORKERS=1\n', 1)
    return block, adapted.replace('/patches/', '/workspace/patches/')


def image_recipe(base_id, source, group, runtime_python_image=None):
    """Build-time dependencies and cache relocation; no test/solver execution."""
    block, adapted = prepare_recipe(source, group)
    spec = recipe(group)
    common = (f'FROM {base_id}\nENTRYPOINT []\nWORKDIR /workspace/repo\n'
              'RUN git config --system user.email eval@cooperbench.local && '
              'git config --system user.name "CooperBench Eval" && '
              'git config --system safe.directory /workspace/repo\n')
    if runtime_python_image:
        common = (f'FROM {runtime_python_image} AS helper_python\n' + common +
                  'COPY --from=helper_python /usr/local /opt/hg-python\n'
                  'ENV LD_LIBRARY_PATH=/opt/hg-python/lib\n')
    install = '#!/bin/bash\nset -e\ncd /workspace/repo\n'
    if spec['family'] == 'python':
        # uv sync can rewrite a tracked lock even with the official command.
        # Preserve its dependency evidence outside the candidate repository and
        # restore the committed input so harness diffs contain candidate edits.
        install += '''if git ls-files --error-unmatch -- uv.lock >/dev/null 2>&1; then
  mkdir -p /opt/hg-cooperbench-locks
  cp uv.lock /opt/hg-cooperbench-locks/input.lock
  git show HEAD:uv.lock > /opt/hg-cooperbench-locks/baseline.lock
fi
'''
        install += block + '\n'
        # Native builds need their build backend after the candidate patch too.
        # pip's temporary build-isolation environments are not a runtime cache.
        install += '''python3 - <<'BUILD_REQUIREMENTS'
import json, pathlib, subprocess, sys
try:
    import tomllib
except ModuleNotFoundError:
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'tomli==2.2.1'], check=True)
    import tomli as tomllib
for path in (pathlib.Path('pyproject.toml'), pathlib.Path('llama-index-core/pyproject.toml')):
    if path.exists():
        build = tomllib.loads(path.read_text()).get('build-system', {})
        requires = build.get('requires', [])
        if requires: subprocess.run([sys.executable, '-m', 'pip', 'install', *requires], check=True)
        if build.get('build-backend'):
            # PEP 660 allows additional editable-build requirements beyond the
            # static PEP 518 list (for example hatchling's editables package).
            # Query the public baseline in its project directory; keep backend
            # imports isolated between the repository and nested core project.
            hook = """import importlib, json, pathlib, subprocess, sys
build = json.loads(sys.argv[1])
sys.path[:0] = [str(pathlib.Path(p).resolve()) for p in build.get('backend-path', [])]
name, _, object_path = build['build-backend'].partition(':')
backend = importlib.import_module(name)
for part in object_path.split('.') if object_path else []: backend = getattr(backend, part)
get_requires = getattr(backend, 'get_requires_for_build_editable', None)
requires = get_requires(None) if get_requires else []
if requires: subprocess.run([sys.executable, '-m', 'pip', 'install', *requires], check=True)
"""
            subprocess.run([sys.executable, '-c', hook, json.dumps(build)], cwd=path.parent, check=True)
BUILD_REQUIREMENTS
rm -rf /workspace/repo/.venv
if [ -f /opt/hg-cooperbench-locks/baseline.lock ]; then
  cp uv.lock /opt/hg-cooperbench-locks/resolved.lock
  cp /opt/hg-cooperbench-locks/baseline.lock uv.lock
fi
'''
    if spec['family'] == 'node':
        install += 'npm install --global pnpm@8\npnpm --version\n'
        common += 'ENV npm_config_offline=true npm_config_cache=/workspace/.npm\n'
    if spec['family'] == 'go':
        install += 'go mod download\nmkdir -p /workspace/.gomodcache\nif [ -d /go/pkg/mod ]; then cp -a /go/pkg/mod/. /workspace/.gomodcache/; fi\n'
        common += ('ENV GOMODCACHE=/workspace/.gomodcache GOCACHE=/workspace/.gocache '
                   'GOPATH=/workspace/.gopath GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local\n')
    if spec['family'] == 'rust' or group.startswith('openai_tiktoken'):
        install += '''mkdir -p /workspace/.cargo
for original in /usr/local/cargo /root/.cargo; do
  for cache in registry git; do
    if [ -d "$original/$cache" ]; then cp -a "$original/$cache" /workspace/.cargo/; fi
  done
done
if [ -d /root/.rustup ]; then
  cp -a /root/.rustup /opt/hg-rustup
  chmod -R a+rX /opt/hg-rustup
  cp -a /root/.cargo/bin /opt/hg-cargo-bin
fi
'''
        common += 'ENV CARGO_HOME=/workspace/.cargo CARGO_NET_OFFLINE=true\n'
        if group.startswith('openai_tiktoken'):
            common += 'ENV RUSTUP_HOME=/opt/hg-rustup PATH=/opt/hg-cargo-bin:$PATH\n'
            # Public baseline tokenizer assets are runtime dependencies. Fetch
            # every released encoding before measurement, without reading any
            # selected tests or applying reference/candidate patches.
            install += '''TIKTOKEN_CACHE_DIR=/workspace/.tiktoken-cache python3 - <<'TOKENIZER_ASSETS'
import tiktoken
for name in tiktoken.list_encoding_names():
    tiktoken.get_encoding(name)
TOKENIZER_ASSETS
'''
            common += 'ENV TIKTOKEN_CACHE_DIR=/workspace/.tiktoken-cache\n'
    # The runtime interpreter must be available to UID 65534, independently of
    # whether the upstream image is Python, Alpine Go, Node or Rust.
    common += 'COPY prepare-dependencies.sh /opt/hg-prepare-dependencies.sh\nRUN bash /opt/hg-prepare-dependencies.sh\n'
    lines = common.splitlines(keepends=True)
    environment = [line for line in lines if line.startswith("ENV ")]
    common = "".join(line for line in lines if not line.startswith("ENV ")) + "".join(environment)
    return common, install, adapted
