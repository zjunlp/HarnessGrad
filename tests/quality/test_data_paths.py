"""One external storage configuration works across adapters, tools and worktrees."""
from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from harnessgrad import paths  # noqa: E402
from harnessgrad.env_file import read_env_file  # noqa: E402
from tools import dataset_lint, datasets  # noqa: E402


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "workspace" / "HarnessGrad"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    monkeypatch.setattr(paths, "CHECKOUT", root)
    for key in os.environ:
        if key.startswith("HG_DATA_") or key.endswith("_DATA") or key in {
                "HG_MAB_PREPARED", "HG_COSTBENCH_RUNTIME", "HG_TAU3_RUNTIME",
                "HG_TAU_BENCH", "HG_TERMINAL_BENCH"}:
            monkeypatch.delenv(key)
    return root


def test_unconfigured_storage_has_no_default_directory(checkout, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    declared = paths.dataset_root("kumo") / "upstream"
    with pytest.raises(paths.DataRootNotConfigured, match="Set HG_DATA_ROOT"):
        declared.is_dir()
    with pytest.raises(paths.DataRootNotConfigured, match="HG_DATA_KUMO"):
        Path(declared)
    assert paths.datasets_root(required=False) is None
    with pytest.raises(paths.DataRootNotConfigured, match="Set HG_DATA_ROOT"):
        paths.datasets_root()
    assert not (checkout.parent / "datasets").exists(), "resolving paths must not create data"
    monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "explicit-data"))
    assert Path(declared) == tmp_path / "explicit-data/kumo/upstream"


def test_dotenv_global_root_and_terminal_override(checkout, tmp_path, monkeypatch):
    (checkout / ".env").write_text("HG_DATA_ROOT='../file data'\nHG_AGENT_API_KEY=fake-test-key\n")
    before = dict(os.environ)
    assert paths.dataset_root("gsm8k") == checkout.parent / "file data" / "gsm8k"
    assert dict(os.environ) == before, "path lookup must not inject model configuration"
    monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "terminal-data"))
    assert paths.dataset_root("gsm8k") == tmp_path / "terminal-data" / "gsm8k"


def test_canonical_individual_override_wins_over_global(checkout, tmp_path, monkeypatch):
    (checkout / ".env").write_text("HG_DATA_KUMO=../special-kumo\nHG_DATA_MEMORYAGENTBENCH=../special-mab\n")
    monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "global"))
    assert paths.dataset_root("kumo") == checkout.parent / "special-kumo"
    assert paths.dataset_root("memoryagentbench") == checkout.parent / "special-mab"
    monkeypatch.setenv("HG_DATA_KUMO", str(tmp_path / "terminal-kumo"))
    assert paths.dataset_root("kumo") == tmp_path / "terminal-kumo"
    monkeypatch.setenv("HG_DATA_KUMO", "")
    assert paths.dataset_root("kumo") == tmp_path / "global" / "kumo"


def test_individual_override_does_not_require_a_shared_root(checkout, tmp_path, monkeypatch):
    monkeypatch.setenv("HG_DATA_KUMO", str(tmp_path / "only-kumo"))
    assert paths.datasets_root(required=False) is None
    assert paths.dataset_root("kumo") == tmp_path / "only-kumo"


@pytest.mark.parametrize("directory,variable", [
    ("kumo", "HG_KUMO_DATA"),
    ("gsm8k", "HG_GSM8K_DATA"),
    ("memoryagentbench", "HG_MAB_DATA"),
    ("tau2-bench", "HG_TAU2_DATA"),
    ("tau3_bench", "HG_TAU3_DATA"),
    ("tau-bench", "HG_TAU_BENCH"),
    ("terminal-bench", "HG_TERMINAL_BENCH"),
    ("strongreject/native-v1", "HG_STRONGREJECT_DATA"),
])
def test_retired_root_names_are_ignored_in_shell_and_dotenv(checkout, tmp_path, monkeypatch,
                                                         directory, variable):
    retired = tmp_path / "retired"
    retired.mkdir()
    (checkout / ".env").write_text(f"{variable}={retired}\n")
    monkeypatch.setenv(variable, str(retired))
    with pytest.raises(paths.DataRootNotConfigured):
        paths.dataset_root(directory).resolve()
    monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "global"))
    assert paths.dataset_root(directory) == tmp_path / "global" / directory
    monkeypatch.setenv(paths.env_name(directory), str(tmp_path / "canonical"))
    assert paths.dataset_root(directory) == tmp_path / "canonical"


def test_worktrees_share_main_config_but_allow_local_overrides(checkout, tmp_path, monkeypatch):
    worktree = tmp_path / "other-directory" / "linked"
    worktree.mkdir(parents=True)
    git_dir = checkout / ".git" / "worktrees" / "linked"
    git_dir.mkdir(parents=True)
    (git_dir / "commondir").write_text("../..\n")
    (worktree / ".git").write_text("gitdir: " + os.path.relpath(git_dir, worktree) + "\n")
    (checkout / ".env").write_text("HG_DATA_ROOT=../shared\n")
    monkeypatch.setattr(paths, "CHECKOUT", worktree)
    assert paths.main_checkout() == checkout
    assert paths.dataset_root("kumo") == checkout.parent / "shared" / "kumo"
    (worktree / ".env").write_text("HG_DATA_ROOT=../local\n")
    assert paths.dataset_root("kumo") == checkout.parent / "local" / "kumo"
    monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "shell"))
    assert paths.dataset_root("kumo") == tmp_path / "shell" / "kumo"
    monkeypatch.delenv("HG_DATA_ROOT")
    (worktree / ".env").unlink()
    (checkout / ".env").unlink()
    with pytest.raises(paths.DataRootNotConfigured):
        paths.dataset_root("kumo").resolve()


def test_tilde_and_secondary_path_overrides(checkout, tmp_path):
    (checkout / ".env").write_text("HG_DATA_ROOT=~/data\nHG_DATA_MEMORYAGENTBENCH_PREPARED=../prepared-only\n")
    assert paths.dataset_root("kumo") == Path.home() / "data" / "kumo"
    assert paths.configured_path("HG_DATA_MEMORYAGENTBENCH_PREPARED", tmp_path / "fallback") == checkout.parent / "prepared-only"


@pytest.mark.parametrize("retired,canonical", [
    ("HG_MAB_PREPARED", "HG_DATA_MEMORYAGENTBENCH_PREPARED"),
    ("HG_COSTBENCH_RUNTIME", "HG_DATA_COSTBENCH_RUNTIME"),
    ("HG_TAU3_RUNTIME", "HG_DATA_TAU3_BENCH_RUNTIME"),
])
def test_retired_secondary_path_names_are_ignored(checkout, tmp_path, monkeypatch, retired, canonical):
    (checkout / ".env").write_text(f"{retired}=../retired\n")
    monkeypatch.setenv(retired, str(tmp_path / "retired"))
    fallback = tmp_path / "default"
    assert paths.configured_path(canonical, fallback) == fallback
    monkeypatch.setenv(canonical, str(tmp_path / "canonical"))
    assert paths.configured_path(canonical, fallback) == tmp_path / "canonical"


@pytest.mark.parametrize("directory", ["", ".", "../outside", "/absolute"])
def test_dataset_directory_cannot_escape_the_shared_root(checkout, directory):
    with pytest.raises(ValueError, match="relative path"):
        paths.dataset_root(directory)


def test_configured_storage_cannot_be_inside_code_even_through_symlinks(checkout, tmp_path, monkeypatch):
    alias = tmp_path / "alias"
    alias.symlink_to(checkout, target_is_directory=True)
    monkeypatch.setenv("HG_DATA_ROOT", str(alias / "datasets"))
    with pytest.raises(ValueError, match="outside the platform"):
        paths.dataset_root("kumo")


def test_scorer_context_skips_discovery_and_restores_host_configuration(checkout, tmp_path, monkeypatch):
    (checkout / ".env").write_text("HG_DATA_ROOT=../configured\n")
    expected = checkout.parent / "configured/kumo"
    assert paths.dataset_root("kumo") == expected

    def forbidden():
        pytest.fail("scoring attempted host configuration discovery")

    with pytest.raises(RuntimeError, match="synthetic scoring failure"):
        with paths.scorer_paths(), monkeypatch.context() as guarded:
            guarded.setattr(paths, "_settings", forbidden)
            guarded.setattr(paths, "main_checkout", forbidden)
            root = paths.dataset_root("kumo")
            assert root == paths.datasets_root() / "kumo"
            # The prefix is a source file, so even a candidate-created data tree
            # cannot be mistaken for a provisioned corpus here.
            assert not root.is_dir()
            assert paths.configured_path("HG_DATA_KUMO", root) == root
            with paths.scorer_paths():
                assert paths.dataset_root("kumo") == root
            assert paths.dataset_root("kumo") == root
            raise RuntimeError("synthetic scoring failure")
    assert paths.dataset_root("kumo") == expected


def test_all_external_adapter_defaults_use_global_config_and_keep_layout(checkout, tmp_path, monkeypatch):
    from data.scaffolds import roots

    shared = tmp_path / "shared-data"
    monkeypatch.setenv("HG_DATA_ROOT", str(shared))
    aliases = {"bfcl_v3": "bfcl-v3", "costbench_dynamic": "costbench",
               "tau2_bench": "tau2-bench", "tau_bench": "tau-bench",
               "terminal_bench": "terminal-bench", "strongreject": "strongreject/native-v1"}
    checked = set()
    for source in (ROOT / "data" / "datasets").glob("*.py"):
        tree = ast.parse(source.read_text())
        kind = next((node.value.value for node in tree.body if isinstance(node, ast.Assign)
                     and isinstance(node.value, ast.Constant)
                     and any(isinstance(t, ast.Name) and t.id == "KIND" for t in node.targets)), None)
        if kind != "dataset":
            continue
        for node in tree.body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == "dataset_root"):
                value = eval(compile(ast.Expression(node.value), str(source), "eval"),
                             {"dataset_root": paths.dataset_root})
                assert value == shared / aliases.get(source.stem, source.stem), source.name
                directory = node.value.args[0].value
                variable = roots.env_name(directory)
                assert paths.env_name(directory) == variable
                assert datasets._logical_data_root(source.stem) == (f"<HG_DATA_ROOT>/{directory}", variable)
                with monkeypatch.context() as override:
                    override.setenv(variable, str(tmp_path / "individual"))
                    assert paths.dataset_root(directory) == tmp_path / "individual"
                checked.add(source.stem)
    assert len(checked) == 55


def test_storage_diagnostics_do_not_discover_configuration_during_scoring(checkout, monkeypatch):
    from data.scaffolds import roots

    def forbidden(*args):
        pytest.fail("scoring diagnostics attempted configuration discovery")

    monkeypatch.setattr(paths, "read_env_file", forbidden)
    with paths.scorer_paths():
        assert paths._settings() == {}
        assert paths.main_checkout() == checkout
        assert not roots.describe("gsm8k")["exists"]


def test_standalone_preparer_reads_dotenv_without_importing_adapter(checkout, tmp_path):
    for relative in ["data/__init__.py", "harnessgrad/__init__.py", "harnessgrad/paths.py", "harnessgrad/env_file.py",
                     "data/datasets/kumo/prepare.py"]:
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    (checkout / ".env").write_text("HG_DATA_ROOT=../configured\n")
    command = "import runpy; print(runpy.run_path(" + repr(str(checkout / "data/datasets/kumo/prepare.py")) + ")[\"DEFAULT_ROOT\"])"
    env = {key: value for key, value in os.environ.items() if key not in {"HG_DATA_ROOT", "HG_DATA_KUMO", "PYTHONPATH"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-c", command], env=env, cwd=tmp_path,
                          text=True, capture_output=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()) == checkout.parent / "configured" / "kumo"


@pytest.mark.parametrize("dataset", ["kumo", "memoryagentbench"])
def test_prepare_help_does_not_require_storage_configuration(checkout, dataset):
    script = ROOT / "data/datasets" / dataset / "prepare.py"
    code = ("import runpy,sys; sys.path.insert(0," + repr(str(ROOT)) + "); "
            "from harnessgrad import paths; from pathlib import Path; paths.CHECKOUT=Path("
            + repr(str(checkout)) + "); sys.argv=[" + repr(str(script)) + ",'--help']; "
            "runpy.run_path(sys.argv[0],run_name='__main__')")
    env = {key: value for key, value in os.environ.items() if not key.startswith("HG_DATA_")}
    proc = subprocess.run([sys.executable, "-I", "-c", code], env=env,
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in proc.stdout


def test_runtime_config_records_the_effective_data_root(checkout):
    from harnessgrad import config
    (checkout / ".env").write_text("HG_DATA_ROOT=../configured\n")
    config.reset()
    try:
        assert config.current().storage.data_root == str(checkout.parent / "configured")
        assert config.load({"HG_DATA_ROOT": "/explicit"}).storage.data_root == "/explicit"
    finally:
        config.reset()


def test_runtime_config_can_record_unconfigured_storage(checkout):
    from harnessgrad import config
    config.reset()
    try:
        assert config.current().storage.data_root is None
    finally:
        config.reset()


def test_missing_storage_preserves_catalogs_and_refuses_data_access(checkout):
    # Storage paths are frozen when an adapter is imported with configured storage. Other
    # files legitimately import AppWorld first; changing the environment afterwards must not
    # pretend to test a fresh unconfigured import. Exercise this boundary in a fresh process
    # instead of deleting/reloading arbitrary adapters in the caller's module cache.
    code = r'''
import sys
from pathlib import Path
from unittest.mock import patch
from harnessgrad import paths
paths.CHECKOUT = Path(sys.argv[1])
from data.scaffolds import registry
from tools import datasets
assert len(registry.published()) == 56
assert len(datasets.datasets_with_downloads(source_fallback=True)) == 56
try:
    registry.load("appworld")
except paths.DataRootNotConfigured as exc:
    assert "Set HG_DATA_ROOT" in str(exc)
else:
    raise AssertionError("unconfigured AppWorld load must refuse data access")
with patch.object(datasets, "_download", side_effect=AssertionError("network attempted")), \
     patch.object(datasets, "_download_adapter", return_value=type("Decl", (), {
         "DATASET_ROOT": paths.dataset_root("kumo")})()):
    assert datasets.main(["--fetch", "--dataset", "kumo"]) == 2
print("unconfigured catalog and data-access boundary: ok")
'''
    proc = subprocess.run([sys.executable, "-c", code, str(checkout)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "unconfigured catalog and data-access boundary: ok" in proc.stdout
    assert "Set HG_DATA_ROOT" in proc.stderr and "Traceback" not in proc.stderr


def test_registry_defers_storage_requirements_to_actual_data_access(checkout, monkeypatch):
    from types import SimpleNamespace
    from data.scaffolds import registry
    root = paths.dataset_root("external-probe")
    called = []

    def load():
        called.append(True)
        (root / "pool.json").read_text()

    monkeypatch.setattr(registry, "importlib", SimpleNamespace(
        import_module=lambda name: SimpleNamespace(DATASET_ROOT=root, load=load)))
    with pytest.raises(paths.DataRootNotConfigured, match="Set HG_DATA_ROOT"):
        registry.load("external-probe")
    assert called == [True], "registry must let the adapter determine whether it needs storage"


def test_registry_does_not_mask_unrelated_import_errors_as_missing_storage(checkout, monkeypatch):
    from types import SimpleNamespace
    from data.scaffolds import registry

    def unavailable(name):
        raise ModuleNotFoundError("unavailable optional runtime")

    monkeypatch.setattr(registry, "importlib", SimpleNamespace(import_module=unavailable))
    with pytest.raises(ModuleNotFoundError, match="unavailable optional runtime"):
        registry.load("tau_bench")


@pytest.mark.parametrize("family,name", [("qa", "bbeh"), ("research", "browsecomp")])
def test_native_family_declarations_do_not_resolve_unconfigured_storage(
        checkout, tmp_path, family, name):
    if family == "qa":
        from data.scaffolds.native_qa import Dataset
        adapter = Dataset(name)
        root = adapter.root
        access = adapter.records
    else:
        from data.scaffolds.native_research.adapter import Adapter
        adapter = Adapter(name)
        root = adapter.root
        access = adapter.manifest
    assert isinstance(root, paths.DatasetPath)
    with pytest.raises(paths.DataRootNotConfigured, match="Set HG_DATA_ROOT"):
        access()


def test_native_qa_refuses_data_inside_the_checkout(tmp_path):
    from data.scaffolds.native_qa import Dataset
    adapter = Dataset("bbeh", ROOT / "data")
    with pytest.raises(ValueError, match="outside the platform tree"):
        adapter.records()


def test_lint_rejects_machine_specific_root_defaults(checkout):
    from types import SimpleNamespace
    source = checkout / "adapter.py"
    source.write_text('DATASET_ROOT = Path("/mnt/server/user/data")\n')
    result = dataset_lint.Result("example")
    dataset_lint.lint_paths(SimpleNamespace(__file__=str(source)), dataset_lint.load_schema(), result)
    assert any("portable" in error for error in result.errors)


def test_download_and_check_use_dotenv_root_before_adapter_can_import(checkout, monkeypatch):
    adapters = checkout / "data" / "datasets"
    adapters.mkdir(parents=True)
    payload = b"released task bytes"
    digest = hashlib.sha256(payload).hexdigest()
    (adapters / "example.py").write_text('''\
from harnessgrad.paths import dataset_root
import unavailable_optional_runtime
KIND = "dataset"
DATASET_ROOT = dataset_root("example")
def downloads():
    return [{"kind": "http", "name": "tasks", "version": "v1",
             "url": "https://example.invalid/tasks", "dst": "upstream",
             "files": ["task.txt"], "sha256": {"task.txt": "'''+digest+'''"}}]
''')
    (checkout / ".env").write_text("HG_DATA_ROOT=../configured\n")
    monkeypatch.setattr(datasets, "ROOT", checkout)

    def cannot_import(name):
        raise ModuleNotFoundError("unavailable_optional_runtime")

    def fetch_bytes(url, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    monkeypatch.setattr(datasets.importlib, "import_module", cannot_import)
    monkeypatch.setattr(datasets, "_download", fetch_bytes)
    rows = datasets.datasets_with_downloads(source_fallback=True)
    expected = checkout.parent / "configured" / "example"
    before = datasets.check_dataset("example", rows[0][1])
    assert before["root"] == str(expected)
    assert not before["present"]
    module = datasets._download_adapter("example")
    ok, message = datasets.fetch_entry(rows[0][1][0], datasets.dataset_root(module))
    assert ok, message
    after = datasets.check_dataset("example", rows[0][1])
    assert after["root"] == str(expected) and after["present"]
    assert "adapter_import_error" in after["status"]
    assert (expected / "upstream/task.txt").read_bytes() == payload


def test_dotenv_parser_matches_driver_format(checkout):
    file = checkout / ".env"
    file.write_text("# comment\n\n HG_DATA_ROOT = '../path with spaces'\nOTHER=has=equals\n=ignored\n")
    assert read_env_file(file) == {"HG_DATA_ROOT": "../path with spaces", "OTHER": "has=equals"}


def test_scienceworld_staged_jre_honors_the_shared_configuration(checkout):
    import importlib.util
    spec = importlib.util.spec_from_file_location("data_paths_scw", ROOT / "data/datasets/scienceworld/lib/scw_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (checkout / ".env").write_text("HG_DATA_ROOT=../configured\n")
    assert module.staged_jre() == checkout.parent / "configured/scienceworld/jre" / module.JRE_DIRNAME


def test_staged_scienceworld_uses_its_local_jre_without_platform_imports(tmp_path):
    source = ROOT / "data/datasets/scienceworld/lib/scw_env.py"
    (tmp_path / "scw_env.py").write_bytes(source.read_bytes())
    script = "import pathlib, scw_env; root=pathlib.Path('.'); jre=root/'jre'/scw_env.JRE_DIRNAME; (jre/'bin').mkdir(parents=True); (jre/'bin/java').touch(); print(scw_env.java_home(root))"
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "HG_SEALED_JAVA_HOME"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                          text=True, capture_output=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert str(tmp_path / "jre") in proc.stdout


def test_lazy_platform_package_keeps_the_public_runner_api():
    import harnessgrad
    from harnessgrad.trusted_runner import TrustedRunnerFacade
    assert harnessgrad.TrustedRunnerFacade is TrustedRunnerFacade
