"""定价与折扣：金额一律整数分（int cents）。"""


def apply_discount(amount_cents, pct):
    """按百分比折扣。"""
    rate = pct / 100.0
    return int(amount_cents * rate)


def member_price(amount_cents, tier):
    """会员价：VIP 88 折，其余原价。"""
    if tier == "VIP":
        return apply_discount(amount_cents, 88)
    return amount_cents
