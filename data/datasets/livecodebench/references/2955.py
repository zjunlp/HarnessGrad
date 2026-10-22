class Solution:
    def accountBalanceAfterPurchase(self, purchaseAmount: int) -> int:
        rounded = ((purchaseAmount + 5) // 10) * 10
        return 100 - rounded
