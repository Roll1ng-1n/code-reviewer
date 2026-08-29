"""支付网关封装。金额为整数分。"""


def capture(payment_id, amount_cents):
    """发起支付确认。"""
    ...


def refund(payment_id, amount_cents):
    """发起退款。"""
    ...
