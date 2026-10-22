import sys

ab, ac, bc = sys.stdin.read().split()
older = {"A": 0, "B": 0, "C": 0}
if ab == "<":
    older["B"] += 1
else:
    older["A"] += 1
if ac == "<":
    older["C"] += 1
else:
    older["A"] += 1
if bc == "<":
    older["C"] += 1
else:
    older["B"] += 1
for name in "ABC":
    if older[name] == 1:
        print(name)
