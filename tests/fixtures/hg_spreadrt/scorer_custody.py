"""Source-only comparison to retained upstream; does not execute upstream or load tasks."""
import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
local = ROOT / 'data/datasets/spreadsheetbench/verify/check.py'
upstream = ROOT / 'registry/w-metriccmp-sources/spreadsheetbench-evaluation.py.txt'
trees = [ast.parse(p.read_text()) for p in (local, upstream)]
functions = [{n.name: n for n in t.body if isinstance(n, ast.FunctionDef)} for t in trees]
for p in (local, upstream):
    print(str(p.relative_to(ROOT)) + ' sha256=' + hashlib.sha256(p.read_bytes()).hexdigest())
for name in ('transform_value', 'datetime_to_float', 'compare_cell_value', 'col_num2name',
             'col_name2num', 'parse_cell_range', 'generate_cell_names'):
    nodes = []
    for f in functions:
        node = f[name]
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:]
        nodes.append(ast.dump(node, include_attributes=False))
    print(name + ' AST_equal_ignoring_docstrings=' + str(nodes[0] == nodes[1]))
print('scope=source-only; adapted workbook/comparison/error policy is separate; no official execution')
