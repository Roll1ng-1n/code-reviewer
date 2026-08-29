"""下单流程：支付与库存的编排。"""
from orders import payments


def place_order(order):
    """下单：支付确认 → 扣减库存。扣减失败抛出异常。"""
    payments.capture(order["payment_id"], order["amount_cents"])
    ok = deduct(order["items"])
    if not ok:
        raise OutOfStock(order["items"])
