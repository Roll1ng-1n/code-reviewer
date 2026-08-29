# sample-repo：迷你订单系统

（原型固定装置：为审查原型提供被审对象与结构地图。）

## 结构地图

```
sample-repo/
├── specs/
│   └── orders.md          # 业务规范（Spec KB）：幂等、货币精度、库存支付顺序、审计
└── src/orders/
    ├── __init__.py
    ├── cancel.py           # 订单取消：幂等取消 + 退款 + 回补库存
    ├── pricing.py          # 定价：折扣与会员价（金额一律整数分）
    ├── inventory.py        # 下单流程：扣库存与支付确认的编排
    ├── payments.py         # 支付网关封装（capture / refund）
    └── audit.py            # 审计日志
```

模块依赖方向：cancel / inventory → payments、audit；pricing 无外部依赖。金额类型全链路为整数分（int cents）。
