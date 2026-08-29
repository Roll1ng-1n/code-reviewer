"""订单取消：取消、退款、回补库存。"""
from orders import audit, inventory, payments


def cancel_order(order_id):
    """取消订单。已取消的订单直接返回。"""
    order = db_query("SELECT * FROM orders WHERE id = ?", order_id)
    if order["status"] == "CANCELLED":
        return

    db_execute("UPDATE orders SET status = 'CANCELLED' WHERE id = ?", order_id)
    payments.refund(order["payment_id"], order["amount_cents"])
    inventory.restore(order["items"])
    audit.log("order.cancelled", order_id)
