"""AST-only, scoped import graph. This is not a Python whole-program proof.

Every source is an entry point, including extensionless Python shebang scripts.
Imports inside functions/conditions count; package initializers count. Computed
loaders, generated code and process launches are inventory, not resolved edges.
No platform or retained upstream module is imported by this analyzer.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import tokenize

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SCOPES = ("eval", "data", "tools", "harnessgrad", "ckpt", "methods")
TARGETS = ("bfcl_eval", "model_handler", "resolve_ast_call", "resolve_ast_by_type",
           "default_decode_ast_prompting")


def source_paths(root):
    paths = {root / "driver.py"}
    for directory in SCOPES:
        for path in (root / directory).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix == ".py":
                paths.add(path)
            elif not path.suffix:
                with path.open("rb") as stream:
                    first = stream.readline(256)
                if first.startswith(b"#!") and b"python" in first:
                    paths.add(path)
    return sorted(paths)


def module_name(path):
    if path.suffix != ".py":
        return "@script:" + path.as_posix()
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return base + "." + node.attr if base else node.attr
    return ""


def aliases(tree):
    """Overapproximate import and simple assignment aliases, ignoring shadowing.

    This catches `import importlib as il`, `from subprocess import run as r`,
    and `load = il.import_module`; arbitrary reflective aliases stay UNKNOWN.
    """
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and not node.level:
            for alias in node.names:
                result[alias.asname or alias.name] = (node.module or "") + "." + alias.name
    for _ in range(8):
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = canonical(dotted(node.value), result)
                for target in node.targets:
                    if isinstance(target, ast.Name) and value and target.id != value:
                        if result.get(target.id) != value:
                            result[target.id] = value
                            changed = True
        if not changed:
            break
    return result


def canonical(name, bindings):
    head, sep, tail = name.partition(".")
    return bindings.get(head, head) + (sep + tail if sep else "")


def mechanism(callee):
    leaf = callee.rsplit(".", 1)[-1]
    if callee in {"__import__", "builtins.__import__", "importlib.import_module"}:
        return "dynamic_import"
    if (callee.startswith("importlib.") and leaf in {
            "find_spec", "spec_from_file_location", "module_from_spec",
            "SourceFileLoader", "SourcelessFileLoader"}) or leaf in {"exec_module", "load_module"}:
        return "file_loader"
    if callee.startswith("runpy."):
        return "runpy"
    if callee in {"exec", "eval", "compile", "builtins.exec", "builtins.eval", "builtins.compile"}:
        return "code_execution"
    if (callee.startswith("subprocess.") or callee.startswith("asyncio.create_subprocess")
            or callee in {"os.system", "os.popen"}
            or callee.startswith(("os.exec", "os.spawn", "os.posix_spawn"))):
        return "process"
    return None


def analyze(root=ROOT):
    paths = source_paths(root)
    sources, trees, texts = [], {}, {}
    for index, path in enumerate(paths):
        relative = path.relative_to(root)
        raw = path.read_bytes()
        with tokenize.open(path) as stream:
            text = stream.read()
        trees[index] = ast.parse(text, filename=relative.as_posix())
        texts[index] = text
        sources.append(dict(id=index, path=relative.as_posix(), module=module_name(relative),
                            bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    modules = {}
    for row in sources:
        modules.setdefault(row["module"], set()).add(row["id"])

    def resolve(name, source):
        # Include every known package initializer, including `from pkg import attr`.
        found = set()
        parts = name.split(".")
        for end in range(1, len(parts) + 1):
            found.update(modules.get(".".join(parts[:end]), ()))
        # Script-local imports (e.g. methods/*/run.py -> methods/editor.py).
        # Multiple possible search roots are conservative edges, not runtime custody.
        for directory in (Path(source["path"]).parent, Path("methods")):
            for suffix in (".py", "/__init__.py"):
                candidate = (directory / (name.replace(".", "/") + suffix)).as_posix()
                found.update(row["id"] for row in sources if row["path"] == candidate)
        return sorted(found)

    edges, mechanisms, references = [], [], []
    adjacency = {row["id"]: set() for row in sources}
    for source in sources:
        index = source["id"]
        tree, text = trees[index], texts[index]
        bindings = aliases(tree)
        package = (source["module"] if source["path"].endswith("/__init__.py")
                   else source["module"].rpartition(".")[0])
        # A file used as an import entry executes its parent packages too, even
        # when no other source has an explicit import edge pointing at this file.
        initializers = [row for row in sources
                        if row["path"].endswith("/__init__.py") and row["id"] != index
                        and source["module"].startswith(row["module"] + ".")]
        if initializers:
            destinations = sorted(row["id"] for row in initializers)
            adjacency[index].update(destinations)
            edges.append(dict(source=index, line=1, kind="entry_initializers",
                              targets=[row["module"] for row in initializers], destinations=destinations,
                              outside_scope=[], statement="<implicit parent package initialization>"))
        for node in ast.walk(tree):
            targets = []
            kind = None
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
                kind = "import"
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".") if package else []
                    if node.level > len(parts):
                        raise ValueError(f"unresolved relative import: {source['path']}:{node.lineno}")
                    base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                targets = [base] + [base + "." + alias.name for alias in node.names if alias.name != "*"]
                kind = "from"
            elif isinstance(node, ast.Call):
                callee = canonical(dotted(node.func), bindings)
                category = mechanism(callee)
                if category:
                    expression = ast.get_source_segment(text, node)
                    literal = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
                    literal_module = (isinstance(literal, str) and not literal.startswith(".")
                                      and callee in {"__import__", "builtins.__import__",
                                                     "importlib.import_module", "runpy.run_module"})
                    mechanisms.append(dict(source=index, path=source["path"], line=node.lineno,
                                           category=category, callee=callee, expression=expression,
                                           resolution="literal_module" if literal_module else "UNKNOWN",
                                           target_mentions=[t for t in TARGETS if t in expression]))
                    if literal_module:
                        targets, kind = [literal], "literal_dynamic"
            if targets:
                destinations = sorted({dest for target in targets for dest in resolve(target, source)})
                adjacency[index].update(destinations)
                outside = []
                for target in targets:
                    for candidate in (root / (target.replace(".", "/") + ".py"),
                                      root / target.replace(".", "/") / "__init__.py"):
                        if candidate.is_file() and candidate not in paths:
                            outside.append(candidate.relative_to(root).as_posix())
                edges.append(dict(source=index, line=node.lineno, kind=kind, targets=targets,
                                  destinations=destinations, outside_scope=sorted(set(outside)),
                                  statement=ast.get_source_segment(text, node)))
            if isinstance(node, (ast.Name, ast.Attribute, ast.Constant)):
                value = (node.value if isinstance(node, ast.Constant) else dotted(node))
                if isinstance(value, str) and any(t in value for t in TARGETS):
                    references.append(dict(source=index, path=source["path"], line=node.lineno,
                                           kind=type(node).__name__, value=value))
    closures = []
    for source in sources:
        pending, seen = [source["id"]], set()
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(adjacency[current] - seen)
        closures.append(dict(entry=source["id"], reachable=sorted(seen)))
    return dict(sources=sources, edges=sorted(edges, key=lambda r: (r["source"], r["line"], r["kind"])),
                mechanisms=sorted(mechanisms, key=lambda r: (r["source"], r["line"], r["callee"])),
                references=sorted(references, key=lambda r: (r["source"], r["line"], r["kind"])),
                closures=closures)


def target_edges(result):
    return [edge for edge in result["edges"]
            if any(t in target for target in edge["targets"] for t in TARGETS)]


def summary(result):
    return dict(sources=len(result["sources"]), entry_points=len(result["closures"]),
                import_nodes=len(result["edges"]), mechanism_nodes=len(result["mechanisms"]),
                unresolved_mechanisms=sum(r["resolution"] == "UNKNOWN" for r in result["mechanisms"]),
                known_target_imports=len(target_edges(result)),
                target_mechanisms=sum(bool(r["target_mentions"]) for r in result["mechanisms"]),
                target_references=result["references"],
                outside_scope_edges=[r for r in result["edges"] if r["outside_scope"]])


if __name__ == "__main__":
    # Explicit artifact creation only; tests recompute and compare without writing.
    result = analyze()
    for key, rows in result.items():
        (HERE / (key + ".jsonl")).write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    observed_summary = json.dumps(summary(result), sort_keys=True)
    (HERE / "summary.json").write_text(observed_summary + "\n")
    print(observed_summary)
