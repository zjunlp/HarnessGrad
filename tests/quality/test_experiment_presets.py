"""`experiments/method-*.json`: one runnable file per published method.

A preset is documentation a reader copies, so the failure that matters is a file that
looks right and dies at launch. Parsing alone does not catch it: `driver.py --show-config`
validates the argparse surface, not the method directory, so a preset naming a method that
was renamed starts, measures the base harness, and reports a number that is not that
method's. Two properties therefore get their own checks below: the method name resolves to
a shipped `run.py`, and the run id is unique in the directory (two files naming one run id
would overwrite each other's curve).

The command-line check has two halves on purpose. One representative preset is actually
run through `driver.py --config <file> --show-config` and must exit 0; that is the contract
the files are documented against. The rest are checked structurally — `from_cli` puts
`--method-entrypoint` on the argv and the script it names exists — because starting the
driver once per file buys nothing the first half did not already prove.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad.experiments import from_cli  # noqa: E402

EXPERIMENTS = ROOT / "experiments"
METHODS = ROOT / "methods"


def presets() -> list[Path]:
    """Every `experiments/method-*.json`, in name order."""
    return sorted(EXPERIMENTS.glob("method-*.json"))


def config_of(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def published_methods() -> dict[str, dict]:
    """`methods/<name>/method.json` for every method that declares `kind: published`."""
    found: dict[str, dict] = {}
    for meta_path in sorted(METHODS.glob("*/method.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(meta, dict) and meta.get("kind") == "published":
            found[meta_path.parent.name] = meta
    return found


# ------------------------------------------------------------------ coverage ---


def test_there_is_at_least_one_preset() -> None:
    """A globbed suite that matched nothing would pass every parametrised test below."""
    assert presets(), "no experiments/method-*.json: the presets are missing"


def test_the_presets_are_exactly_the_published_methods() -> None:
    """One file per published port, and none for a method that is not a published port.

    The second half is the rule from `tools/method_lint.py`: `reference`, `control`,
    `other` and `improver` are the platform's own entrypoints. They stay launchable by
    hand, but a baseline table reads only `published`, so a preset must not quietly
    promote one of them into it.
    """
    published = published_methods()
    assert published, "no method declares kind: published; the catalogue moved"

    files: set[str] = set()
    for path in presets():
        config = config_of(path)
        method = config.get("method")
        assert isinstance(method, str) and method, f"{path.name}: no method name"
        assert path.name == f"method-{method}.json", (
            f"{path.name}: names the method {method!r}, so the file should be "
            f"method-{method}.json")
        files.add(method)

    for name in published:
        assert (EXPERIMENTS / f"method-{name}.json").is_file(), (
            f"method {name!r} is published but has no experiments/method-{name}.json")

    assert files == set(published), (
        "presets exist for methods that are not published: "
        f"{sorted(files - set(published))}; published methods with no preset: "
        f"{sorted(set(published) - files)}")


# -------------------------------------------------------------- the files ---


@pytest.mark.parametrize("path", presets(), ids=lambda path: path.name)
def test_a_preset_parses_and_names_a_shipped_published_method(path: Path) -> None:
    """Every property a preset claims, checked against this checkout."""
    config = config_of(path)  # a JSONDecodeError is the first failure
    assert isinstance(config, dict), f"{path.name}: an experiment is a JSON object"

    name = config["method"]
    method_dir = METHODS / name
    assert (method_dir / "run.py").is_file(), (
        f"{path.name}: methods/{name}/run.py does not exist")

    meta_path = method_dir / "method.json"
    assert meta_path.is_file(), f"methods/{name}/method.json is missing"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta.get("kind") == "published", (
        f"methods/{name}/method.json is kind {meta.get('kind')!r}, not 'published'")

    # The documented shape: the smoke test, not a benchmark protocol.
    assert config["dataset"] == "gsm8k", path.name
    assert config["side"] == "train", path.name
    assert config["sampling"] == "random_5", path.name
    assert config["rounds"] == 1, path.name
    assert config["jobs"] == 1, path.name
    assert config["run_id"] == f"{name}-quickstart", path.name

    # Two shape fields follow the method's own declaration instead of one universal value,
    # because a method that cannot start on the reference harness has no honest preset that
    # names it. The declaration is `method.json:requires`; the default stays what the nine
    # original presets use, so a method that declares nothing is still held to `loop`.
    requires = meta.get("requires") if isinstance(meta.get("requires"), dict) else {}
    if requires.get("instruction_file"):
        # One of the four files `policy_ports.runtime.instruction_path` can find. `loop`
        # ships none, which is why its preset would refuse before any model call.
        assert config["harness"] != "loop", path.name
        tree = ROOT / "base_harness" / config["harness"]
        found = [rel for rel in ("prompts/system.md", "prompt.md", "CLAUDE.md", "AGENTS.md")
                 if (tree / rel).is_file()]
        assert len(found) == 1, (
            f"{path.name}: harness {config['harness']!r} declares {found} instruction files, "
            "and the port needs exactly one to resolve")
    else:
        assert config["harness"] == "loop", path.name
    assert config["trials"] == max(1, int(requires.get("min_trials", 1))), (
        f"{path.name}: trials must be the method's declared minimum")


@pytest.fixture
def authored_gsm8k_provider(tmp_path, monkeypatch):
    """Real provider admission with an authored JSON reader in a managed runtime.

    The trusted installer stand-in supplies only the tiny pyarrow surface used
    by this fixture. No released GSM8K rows, real Parquet or host packages are read.
    Descriptor, installed-package, artifact and child-interpreter checks stay real.
    """
    import stat
    from harnessgrad import dataset_runtime

    root = tmp_path / "authored-gsm8k"
    (root / "main").mkdir(parents=True)
    for split in ("train", "test"):
        rows = [{"question": f"Authored {split} arithmetic control", "answer": "#### 42"}]
        (root / "main" / f"{split}-00000-of-00001.parquet").write_text(json.dumps(rows))
    monkeypatch.setenv("HG_DATA_GSM8K", str(root))
    monkeypatch.setenv("HG_DATASET_RUNTIME_ROOT", str(tmp_path / "provider-store"))
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    original_run = dataset_runtime._run

    def install(argv, *, timeout_s=None, allow_downloads=False):
        args = [str(arg) for arg in argv]
        if args[1:3] != ["pip", "install"]:
            return original_run(argv, timeout_s=timeout_s, allow_downloads=allow_downloads)
        assert allow_downloads is True
        assert Path(args[-1]) == ROOT / "data/datasets/gsm8k/requirements-load.txt"
        python = Path(args[args.index("--python") + 1])
        site = python.parent.parent / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        package = site / "pyarrow"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text('"""Authored JSON transport, never real Arrow."""\n')
        (package / "parquet.py").write_text(
            "import json\nfrom pathlib import Path\n"
            "class Table:\n"
            "    def __init__(self, rows): self.rows = rows\n"
            "    def to_pylist(self): return self.rows\n"
            "def read_table(path, columns=None):\n"
            "    rows = json.loads(Path(path).read_text())\n"
            "    return Table(rows if columns is None else [{k: row[k] for k in columns} for row in rows])\n")
        metadata = site / "pyarrow-23.0.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: pyarrow\nVersion: 23.0.0\n")
        return ""

    monkeypatch.setattr(dataset_runtime, "_run", install)
    try:
        selected = dataset_runtime.prepare("gsm8k", python=sys.executable)
        assert dataset_runtime.resolve("gsm8k")["runtime_id"] == selected["runtime_id"]
        yield selected
    finally:
        for path in (tmp_path / "provider-store").rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o700)


def test_every_preset_dataset_resolves_to_a_provisioned_adapter(authored_gsm8k_provider) -> None:
    """Preset names resolve through real provider admission with authored local data.

    This checks the preset/adapter wiring without asserting that a developer has
    downloaded benchmark data. The provider still verifies the managed artifact
    and launches its exact interpreter to inspect these synthetic files.
    """
    from data.scaffolds import registry

    with registry.provider_session():
        for name in sorted({config_of(path)["dataset"] for path in presets()}):
            assert registry.kind_of(name) == "dataset", f"{name!r} is not a dataset adapter"
            status = registry.provision_status(name)
            assert status is not None, f"{name!r} does not declare provision status"
            assert status.get("present") is True, status
            assert status["tasks"] == 2
        assert registry.provider_runtime("gsm8k")["runtime_id"] == authored_gsm8k_provider["runtime_id"]


# ----------------------------------------------------------------- run ids ---


def test_run_ids_are_unique_across_the_directory() -> None:
    """Two experiment files may not name the same run: the second would overwrite it."""
    seen: dict[str, str] = {}
    for path in sorted(EXPERIMENTS.glob("*.json")):
        config = config_of(path)
        run_id = config.get("run_id")
        if run_id is None:  # `null` drops the flag and the driver chooses a run id
            continue
        assert run_id not in seen, (
            f"{path.name} reuses run id {run_id!r}, already used by {seen[run_id]}")
        seen[run_id] = path.name


# ------------------------------------------------------------------- argv ---


def test_the_representative_preset_is_accepted_by_driver_show_config() -> None:
    """One real `driver.py --config <file> --show-config` run, exit code and all.

    `--show-config` is the driver's own answer that the expanded flags parse; it loads no
    dataset, probes no improver and calls no model, so it is cheap enough to run here.
    """
    path = presets()[0]
    proc = subprocess.run(
        [sys.executable, "driver.py", "--config", str(path), "--show-config"],
        cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, (
        f"driver.py rejected {path.name}:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    printed = json.loads(proc.stdout)
    assert isinstance(printed, dict) and printed, "--show-config printed no configuration"


@pytest.mark.parametrize("path", presets(), ids=lambda path: path.name)
def test_the_argv_names_the_method_script_that_exists(path: Path) -> None:
    """The other half of the contract: `from_cli` points at this checkout's `run.py`."""
    config = config_of(path)
    name = config["method"]
    argv = from_cli(path, [])
    assert "--method-entrypoint" in argv, f"{path.name}: no --method-entrypoint in {argv}"
    value = argv[argv.index("--method-entrypoint") + 1]

    # A bare method name is completed to `<interpreter> <checkout>/methods/<name>/run.py`
    # (`harnessgrad/experiments.py:_method`), the absolute form the driver requires.
    parts = shlex.split(value)
    assert len(parts) >= 2, f"{path.name}: {value!r} is not an interpreter plus a script"
    script = Path(parts[1])
    assert script.is_absolute(), f"{path.name}: {script} is not absolute"
    assert script == METHODS / name / "run.py", f"{path.name}: {script} names another method"
    assert script.is_file(), f"{path.name}: {script} does not exist"
