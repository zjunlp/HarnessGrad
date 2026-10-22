import sys


def main() -> None:
    left, right = map(int, sys.stdin.read().split())
    if left and right or not left and not right:
        print("Invalid")
    elif left:
        print("Yes")
    else:
        print("No")


main()
