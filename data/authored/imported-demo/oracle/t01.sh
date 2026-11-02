#!/usr/bin/env bash
# 参考解：把 broken.py 里的减法改回加法，然后跑材料自带的测试。
# 它必须让检查器判 1.0 —— 这是第一道门。
set -e
cat > broken.py <<'PY'
def add(a, b):
    return a + b
PY
python3 test_broken.py
