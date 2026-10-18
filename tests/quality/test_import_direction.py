"""Static import graph and reviewed layer boundaries; see docs/architecture.md.

Run with pytest, or directly with Python (stdlib only). ``--graph`` prints the
current module -> imported modules graph without importing production code.
"""
from __future__ import annotations

import ast
import json
import sys
import tokenize
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("data", "eval", "harnessgrad", "tools")

# Exact file/module edges, not permission for all of eval to depend on config.
ALLOWED_EVAL_CONFIG_EDGES = {
    ("eval/service/judge.py", "harnessgrad.config"):
        "Reads only the explicitly configured verifier judge identity and credential.",
    ("eval/containers/harness_runtime.py", "harnessgrad.config"):
        "Reads the resolved runtime overlay configuration.",
    ("eval/containers/sandbox.py", "harnessgrad.config"):
        "Reads the resolved sandbox configuration.",
    ("eval/service/services.py", "harnessgrad.config"):
        "Reads the resolved service configuration.",
}

# Existing layering debt reviewed at 6c539d8. Keys are (rule, file, module), so
# neither a whole control directory nor other imports from these files are exempt.
# Remove an entry when its dependency is moved; stale entries fail the guard.
EXCEPTIONS = {
    ("data-eval", "data/datasets/strongreject/control.py", "eval.run.runner"):
        "Explicit dataset control uses the platform execution path, never the actor API.",
    ("data-eval", "data/datasets/strongreject/control.py", "eval.verification.verifier"):
        "Explicit dataset control exercises the trusted judge wrapper in its real sandbox.",
    ("data-eval", "data/datasets/kumo/control.py", "eval.run.runner"):
        "The dataset-local control validates SETUP -> oracle harness -> VERIFY, as ALFWorld does.",
    ("data-eval", "data/datasets/kumo/control.py", "eval.run.tool_relay"):
        "The local control starts and cleans its selected private relay without changing the runner.",
    ("data-eval", "data/datasets/kumo/control.py", "eval.service.tool_transport"):
        "The local control performs an actual query through the existing shared transport.",
    ("data-eval", "data/datasets/kumo/relay.py", "eval.service.tool_transport"):
        "The private KUMO server program reuses the platform transport, as ALFWorld does.",
    ("data-eval", "data/datasets/agentdojo/relay.py", "eval.service.tool_transport"):
        "The dataset's host-side environment server needs the case and the dataset runtime, so it"
        " lives dataset-side; it serves through the platform's one transport instead of writing a"
        " second implementation of the wire format.",
    ("data-eval", "data/datasets/alfworld/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one to validate adapter wiring.",
    ("data-eval", "data/datasets/alfworld/relay.py", "eval.service.tool_transport"):
        "The host-side ALFWorld environment server is a program, not a library: it serves "
        "the platform's one tool transport, which lives in eval/service.",
    ("data-eval", "data/datasets/appworld/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one with the AppWorld verifier.",
    ("data-eval", "data/datasets/appworld.py", "eval.policy.appworld_reporting"):
        "The adapter shares make_manifest with reporting; moving it needs a separate refactor.",
    ("data-eval", "data/scaffolds/command_control.py", "eval.run.runner"):
        "The shared command control invokes run_one for platform verification.",
    ("data-eval", "data/datasets/jericho/control.py", "eval.policy.runtime_policy"):
        "The local control stages the declared runtime and applies the platform timeout.",
    ("data-eval", "data/datasets/jericho/control.py", "eval.containers.sandbox"):
        "The local control executes the game CLI in the platform sandbox.",
    ("data-eval", "data/datasets/jericho/control.py", "eval.verification.verifier"):
        "The local control uses the platform verifier to interpret measured evidence.",
    ("data-eval", "data/datasets/jericho/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one with the Jericho verifier.",
    ("data-eval", "data/scaffolds/qa/control.py", "eval.run.runner"):
        "The shared QA control invokes run_one to validate adapter wiring.",
    ("data-eval", "data/datasets/scienceworld/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one with the ScienceWorld verifier.",
    ("data-eval", "data/datasets/spreadsheetbench/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one with the spreadsheet verifier.",
}


@dataclass(frozen=True)
class ImportEdge:
    file: str
    source: str
    target: str
    line: int
    statement: str


def _module_name(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _within(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _imports(file: str, text: str, modules: set[str]) -> list[ImportEdge]:
    """Resolve absolute/relative imports, including function and conditional ones.

    For ``from package import child``, a known child module is an edge to that
    child; an imported attribute is an edge to the containing module. Namespace
    packages and __init__.py re-exports use the same module index.
    """
    path = Path(file)
    source = _module_name(path)
    package = source if path.name == "__init__.py" else source.rpartition(".")[0]
    edges = []
    for node in ast.walk(ast.parse(text, filename=file)):
        if isinstance(node, ast.Import):
            targets = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                if node.level > len(parts):
                    raise ValueError(f"{file}:{node.lineno}: relative import beyond package")
                base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
            targets = set()
            for alias in node.names:
                child = base + "." + alias.name
                targets.add(child if child in modules else base)
        else:
            continue
        statement = " ".join(ast.get_source_segment(text, node).split())
        edges.extend(ImportEdge(file, source, target, node.lineno, statement)
                     for target in sorted(targets))
    return edges


def _source_paths(root: Path) -> list[Path]:
    return sorted(path for directory in SOURCE_DIRS
                  for path in (root / directory).rglob("*.py")
                  if "__pycache__" not in path.parts) + [root / "driver.py"]


def _source_imports(root: Path) -> list[ImportEdge]:
    paths = _source_paths(root)
    modules = {_module_name(path.relative_to(root)) for path in paths}
    # Include namespace packages, even when they do not have __init__.py.
    for name in list(modules):
        parts = name.split(".")
        modules.update(".".join(parts[:i]) for i in range(1, len(parts)))
    edges = []
    for path in paths:
        with tokenize.open(path) as stream:
            edges.extend(_imports(path.relative_to(root).as_posix(), stream.read(), modules))
    return edges


def _adapter_namespace(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 3 and parts[:2] == ["data", "datasets"]:
        return ".".join(parts[:3])
    return None


def _rule(edge: ImportEdge) -> str | None:
    adapter = _adapter_namespace(edge.source)
    if (adapter and _within(edge.target, "data")
            and not any(_within(edge.target, shared)
                        for shared in ("data.scaffolds", "data.runtimes"))
            and not _within(edge.target, adapter)):
        return "adapter-data"
    if (any(_within(edge.source, name) for name in
            ("harnessgrad.config", "harnessgrad.runtime"))
            and edge.target.split(".")[0] in {"eval", "data", "tools", "driver"}):
        return "foundation-direction"
    if (edge.source.split(".")[0] in {"data", "eval", "harnessgrad"}
            and _within(edge.target, "legacy")):
        return "production-legacy"
    if _within(edge.source, "data") and _within(edge.target, "eval"):
        return "data-eval"
    if _within(edge.source, "eval") and _within(edge.target, "harnessgrad.config"):
        return "eval-config"
    return None


def _policy_errors(edges: list[ImportEdge], exceptions=EXCEPTIONS,
                   allowed_config_edges=ALLOWED_EVAL_CONFIG_EDGES) -> list[str]:
    permitted = {("eval-config", file, target): reason
                 for (file, target), reason in allowed_config_edges.items()}
    permitted.update(exceptions)
    observed = set()
    errors = []
    for edge in edges:
        rule = _rule(edge)
        if rule is None:
            continue
        key = (rule, edge.file, edge.target)
        observed.add(key)
        if key not in permitted:
            errors.append(f"{edge.file}:{edge.line}: {edge.statement} "
                          f"[{rule}: {edge.source} -> {edge.target}]")
    for key, reason in sorted(permitted.items()):
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{key}: permission must have a review reason")
        if key not in observed:
            errors.append(f"{key}: stale permission; remove it with the import")
    return errors


class ImportDirectionTests(unittest.TestCase):
    def test_production_imports_use_canonical_eval_paths(self):
        compatibility_modules = {
            "eval." + path.stem for path in (ROOT / "eval").glob("*.py")
        }
        stale = [edge for edge in _source_imports(ROOT)
                 if edge.target in compatibility_modules
                 and edge.source not in compatibility_modules]
        self.assertFalse(stale, "Import the canonical eval implementation:\n" + "\n".join(
            f"{edge.file}:{edge.line}: {edge.statement}" for edge in stale))

    def test_scaffolds_and_adapter_asset_directories_are_distinct(self):
        # KIND is the publication declaration, so a scaffold must never declare it.
        for path in (ROOT / "data/scaffolds").rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Name) and t.id == "KIND" for t in targets):
                        self.assertNotEqual(ast.literal_eval(node.value), "dataset", str(path))
        # pinchbench is unclassified inventory, deliberately not adapter assets.
        # `data.runtimes` holds the platform's own sealed-runtime closure pins: pure data, no
        # imports, shared by adapters exactly like `data.scaffolds`. It was added by the sealed
        # native-runtime work and never classified here, which made every adapter that imports
        # `data.runtimes.NATIVE_PINS` an unclassified data directory / a forbidden edge.
        infrastructure = {"scaffolds", "datasets", "__pycache__", "pinchbench", "runtimes"}
        for directory in (ROOT / "data").iterdir():
            if directory.is_dir():
                self.assertIn(directory.name, infrastructure, f"unclassified data directory: {directory}")
        adapters = ROOT / "data/datasets"
        for directory in adapters.iterdir():
            if not directory.is_dir() or directory.name == "__pycache__":
                continue
            self.assertTrue(directory.with_suffix(".py").is_file(),
                            f"assets lack their own adapter: {directory}")
            for path in directory.rglob("*.py"):
                for node in ast.parse(path.read_text(), filename=str(path)).body:
                    if isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        if any(isinstance(t, ast.Name) and t.id == "KIND" for t in targets):
                            self.assertNotEqual(ast.literal_eval(node.value), "dataset", str(path))
        # A top-level adapter (including a compatibility shim) defeats the split.
        fixtures = {"__init__", "demo", "probe_set", "broken_service",
                    "verify_demo", "verify_exec", "verify_service"}
        self.assertEqual({p.stem for p in (ROOT / "data").glob("*.py")}, fixtures)
        for old in ("qa", "registry.py", "command_control.py", "command_oracle_harness"):
            self.assertFalse((ROOT / "data" / old).exists(), f"old scaffold shim: {old}")

    def test_publication_preserves_the_ordered_30_adapter_names(self):
        from data.scaffolds import registry
        expected = ('agentdojo', 'aime', 'alfworld', 'api_bank', 'appworld', 'bfcl_v3', 'bird_minidev', 'chessbench_puzzles', 'ds1000', 'fever', 'gaia', 'gsm8k', 'hotpotqa', 'jericho', 'kumo', 'livecodebench', 'locomo', 'musique', 'nestful', 'physicsquestions', 'reasoning_gym', 'scienceworld', 'sotuqa', 'sportsunderstanding', 'spreadsheetbench', 'strategyqa', 'tau2_bench', 'tau_bench', 'terminal_bench', 'triviaqa', 'twowikimultihopqa')
        self.assertEqual(registry.published(), expected)
        for name in expected:
            module = registry.importlib.import_module(registry.module_name(name))
            self.assertEqual(module.__name__, f"data.datasets.{name}")
            self.assertEqual(Path(module.__file__).resolve(), ROOT / "data/datasets" / f"{name}.py")
            self.assertFalse((ROOT / "data" / f"{name}.py").exists())
        self.assertEqual(registry.module_name("demo"), "data.demo")

    def test_current_source_layering(self):
        for directory in SOURCE_DIRS:
            self.assertTrue((ROOT / directory).is_dir(), f"missing source directory: {directory}")
        errors = _policy_errors(_source_imports(ROOT))
        self.assertFalse(errors, "Import direction violations:\n" + "\n".join(errors))

    def test_forbidden_import_forms(self):
        cases = [
            ("harnessgrad/config/areas.py", "import eval.run.runner as runner"),
            ("harnessgrad/config/__init__.py", "from data.scaffolds import registry"),
            ("harnessgrad/config/loader.py", "from tools.validate_run import main"),
            ("harnessgrad/config/loader.py", "from driver import main"),
            ("harnessgrad/runtime/events.py", "import eval"),
            ("harnessgrad/runtime/events.py", "import data.scaffolds.registry"),
            ("harnessgrad/runtime/events.py", "import tools.validate_run"),
            ("harnessgrad/runtime/events.py", "import driver as cli"),
            ("data/new_adapter.py", "def load():\n    from eval import runner"),
            ("data/new_adapter.py", "if False:\n    import eval.run.runner"),
            ("data/new_adapter.py", "from legacy import adapter"),
            ("eval/new_runner.py", "from legacy.compat import run"),
            ("harnessgrad/new_runtime.py", "import legacy.compat as compat"),
            ("eval/new_runner.py", "from harnessgrad import config"),
            # An approved file cannot add a dependency on a config submodule.
            ("eval/service/services.py", "from harnessgrad.config import loader"),
            # A directory/file exception cannot admit a different eval module.
            ("data/scaffolds/qa/control.py", "from eval.service.services import start"),
        ]
        modules = {"eval.run.runner", "data.scaffolds.registry", "harnessgrad.config",
                   "harnessgrad.config.loader"}
        for file, text in cases:
            with self.subTest(file=file, text=text):
                errors = _policy_errors(_imports(file, text, modules), {}, {})
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(file + ":", errors[0])
                self.assertIn(" ".join(text.splitlines()[-1].split()), errors[0])

    def test_adapter_data_dependencies_stay_private_or_use_scaffolds(self):
        modules = {"data.datasets.alpha", "data.datasets.beta", "data.demo",
                   "data.scaffolds.registry", "data.datasets.alpha.runtime"}
        for statement in ("import data.datasets.beta", "from data.datasets import beta",
                          "from .. import beta", "import data.demo",
                          "import data.registry", "import data.qa.base"):
            with self.subTest(statement=statement):
                edges = _imports("data/datasets/alpha/control.py", statement, modules)
                self.assertTrue(_policy_errors(edges, {}, {}))
        for statement in ("from data.scaffolds import registry",
                          "from data.scaffolds.qa import base",
                          "from . import runtime", "import json"):
            with self.subTest(statement=statement):
                edges = _imports("data/datasets/alpha/__init__.py", statement, modules)
                self.assertEqual(_policy_errors(edges, {}, {}), [])

    def test_relative_imports_and_module_attributes(self):
        cases = [
            ("harnessgrad/runtime/__init__.py", "from .events import Stage", "harnessgrad.runtime.events"),
            ("eval/run/runner.py", "from ..containers import sandbox as sb", "eval.containers.sandbox"),
            ("harnessgrad/runtime/compat.py", "from ..config import current", "harnessgrad.config"),
            ("eval/sub/runner.py", "from ..service import services", "eval.service.services"),
            ("eval/run/runner.py", "from harnessgrad import config as cfg", "harnessgrad.config"),
            ("eval/run/runner.py", "from harnessgrad.config import *", "harnessgrad.config"),
        ]
        modules = {"eval.containers.sandbox", "eval.service.services", "harnessgrad.config"}
        for file, text, target in cases:
            with self.subTest(file=file, text=text):
                edges = _imports(file, text, modules)
                self.assertEqual([edge.target for edge in edges], [target])

    def test_permissions_are_exact_and_require_reasons(self):
        edges = _imports("data/example.py", "from eval.run.runner import run_one", set())
        key = ("data-eval", "data/example.py", "eval.run.runner")
        self.assertEqual(_policy_errors(edges, {key: "Existing control dependency."}, {}), [])
        self.assertIn("review reason", "\n".join(_policy_errors(edges, {key: " "}, {})))
        self.assertIn("stale permission", "\n".join(_policy_errors([], {key: "Old edge."}, {})))
        allowed = {("eval/service/services.py", "harnessgrad.config"): "Reads service configuration."}
        edges = _imports("eval/service/services.py", "from harnessgrad import config", {"harnessgrad.config"})
        self.assertEqual(_policy_errors(edges, {}, allowed), [])
        self.assertIn("stale permission", "\n".join(_policy_errors([], {}, allowed)))

    def test_permissions_do_not_admit_neighboring_files_or_modules(self):
        cases = [
            ("data/example.py", "eval.run.runner", "eval.service.services", "data/other.py", "data-eval"),
            ("eval/service/services.py", "harnessgrad.config", "harnessgrad.config.loader",
             "eval/other.py", "eval-config"),
        ]
        for file, target, other_target, other_file, rule in cases:
            with self.subTest(rule=rule):
                permitted = {(rule, file, target): "Reviewed existing edge."}
                edges = _imports(file, f"import {target}\nimport {other_target}", set())
                edges += _imports(other_file, f"import {target}", set())
                errors = _policy_errors(edges, permitted, {})
                self.assertEqual(len(errors), 2, errors)
                self.assertTrue(any(f"{file}:2: import {other_target}" in error for error in errors))
                self.assertTrue(any(f"{other_file}:1: import {target}" in error for error in errors))

    def test_unrelated_layers_and_import_text_are_not_banned(self):
        cases = [
            ("harnessgrad/trusted_runner.py", "from eval.run.runner import run_one"),
            ("data/example.py", "import evaluation\nimport legacy_helpers"),
            ("harnessgrad/runtime/events.py", "from ..config import current"),
            ("data/example.py", '# import eval.run.runner\ntext = "import legacy"'),
        ]
        for file, text in cases:
            with self.subTest(file=file, text=text):
                self.assertEqual(_policy_errors(_imports(file, text, set()), {}, {}), [])


if __name__ == "__main__":
    if sys.argv[1:] == ["--graph"]:
        graph = {_module_name(path.relative_to(ROOT)): set() for path in _source_paths(ROOT)}
        for edge in _source_imports(ROOT):
            graph[edge.source].add(edge.target)
        print(json.dumps({name: sorted(targets) for name, targets in sorted(graph.items())}, indent=2))
    else:
        unittest.main()
