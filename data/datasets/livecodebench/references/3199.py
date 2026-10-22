class Solution:
    def distributeCandies(self, n: int, limit: int) -> int:
        return sum(1 for a in range(limit + 1) for b in range(limit + 1)
                   if 0 <= n - a - b <= limit)
