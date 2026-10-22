import sys

order = sys.stdin.read().strip()
print("Yes" if order.index("R") < order.index("M") else "No")
