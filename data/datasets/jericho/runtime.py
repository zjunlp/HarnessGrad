"""Project executable Jericho code without reading reference-bearing package data.

Only these files are read. game_info.py, bytecode, package archives and auxiliary
JSON data never enter the projection. The installed package remains untouched.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

CODE_FILES = ("__init__.py", "version.py", "jericho.py", "defines.py",
              "util.py", "template_action_generator.py")


class _PublicCode(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        if node.module == "game_info":
            return None
        return node

    def visit_Assign(self, node):
        if any(isinstance(target, ast.Name) and target.id == "BINDINGS_DICT"
               for target in node.targets):
            node.value = ast.Dict(keys=[], values=[])
        return node

    def _without_docs(self, node):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant) \
                and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        if not node.body:
            node.body = [ast.Pass()]
        return node

    visit_Module = _without_docs
    visit_ClassDef = _without_docs

    def visit_FunctionDef(self, node):
        self._without_docs(node)
        if node.name == "get_walkthrough":
            node.body = [ast.Raise(exc=ast.Call(
                func=ast.Name(id="RuntimeError", ctx=ast.Load()),
                args=[ast.Constant("reference data is unavailable in the agent runtime")],
                keywords=[]), cause=None)]
        if node.name == "seed":
            # Upstream uses `seed or ...`, which changes the official seed 0 to -1
            # when bindings are absent. Preserve explicit zero in this projection.
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "seed" for t in stmt.targets):
                    stmt.value = ast.IfExp(
                        test=ast.Compare(left=ast.Name(id="seed", ctx=ast.Load()),
                                         ops=[ast.IsNot()], comparators=[ast.Constant(None)]),
                        body=ast.Name(id="seed", ctx=ast.Load()), orelse=ast.Constant(-1))
        return node


@lru_cache(maxsize=4)
def agent_runtime(package_dir: str) -> dict:
    package = Path(package_dir)
    library = package / "libfrotz.so"
    if library.is_symlink() or not library.is_file():
        raise ValueError("runtime native library must be a regular file")
    files = []
    for name in CODE_FILES:
        source = package / name
        if source.is_symlink():
            raise ValueError(f"runtime code cannot be a symlink: {source}")
        tree = _PublicCode().visit(ast.parse(source.read_text()))
        files.append({"path": name, "content": ast.unparse(ast.fix_missing_locations(tree)) + "\n"})
    files.append({"path": "libfrotz.so", "from": str(library.resolve())})
    return {"package_dir": str(package.resolve()), "files": files}
