# PROTOTYPE (throwaway) — risk-p1：业务逻辑一致性风险验证

> 一次性原型：回答 Wayfinder 票 [#9](https://github.com/Roll1ng-1n/code-reviewer/issues/9) 的问题，不进生产代码。

## 问题

只凭「结构地图 + diff + 变更文件新内容 + Spec KB」，单次强模型调用能否发现**真实形态**（表面有防护、happy path 可跑通）的业务逻辑违规？

## 运行

```
python prototype/risk-p1/run.py        # 需环境变量 DEEPSEEK_API_KEY，零第三方依赖
```

## 设计

- `sample-repo/`：迷你订单系统，`specs/orders.md` 含 4 条成文业务规则（幂等性 / 货币精度 / 库存与支付顺序 / 审计），README 为结构地图
- 3 个注入 case，各违反一条规则，且刻意做成微妙形态：
  - case1 取消幂等：**有防护假象**——存在 `if status == 'CANCELLED': return` 守卫，但 check-then-act 竞态仍会双退款
  - case2 货币精度：float 运算 + `int()` 截断，替换整数分先乘后除 + 银行家舍入
  - case3 库存支付顺序：先支付后扣库存（happy path 完全可跑通）
- 上下文配方：结构地图（README）+ Spec KB 全文 + diff + 变更文件新内容（import 邻域 depth-0 的替身）
- 模型：deepseek-chat，temperature=0，response_format=json
- 评分：文件命中 + 规则引用（rationale/message 含规则关键词）→ 检出；另记录严重度、行号偏移、误报数、延迟

## 结果

运行时间 2026-08-29，deepseek-chat，temp=0，单次运行：

| Case | 检出 | severity | category | 行号偏移 | 误报 | findings 数 | 延迟 |
|---|---|---|---|---|---|---|---|
| case1 取消幂等（check-then-act 带防护假象） | ✅ | blocker | spec | -3 | 0 | 1 | 2.1s |
| case2 货币精度（float + 截断） | ✅ | blocker | spec | 0 | 0 | 1 | 2.4s |
| case3 库存支付顺序颠倒 | ✅ | blocker | spec | 0 | 0 | 1 | 2.1s |

**结论：核心风险对该配方范围退役。** 最小上下文配方（结构地图 + diff + 变更文件新内容 + Spec KB）足以支撑 T1/T2 型规格违规检出；三条 rationale 全部正确引用规则章节（§幂等性 / §货币精度 / §库存与支付顺序），无幻觉、无空泛 finding。

**诚实告警（写进规格风险章节）**：

1. 样本 n=3、单次运行——这是风险验证不是统计结论；真正的测量交给 #10 golden set（20-50 PR）+ ≥3 次重复
2. 违规作者=评分作者——「规范说 X、代码非 X」的形态比真实世界的微妙违规更易匹配；真实检验靠 #10 的真实 PR
3. **T3（跨文件）未测**——三个 case 都是「规则知识 + 变更文件内」可判定的；SWE-PRBench 显示 Type 3 是业界公认的难区，import 邻域深度的价值要靠 #10 的 T3 分层样本验证
4. 中文规范+中文注释语料，deepseek-chat 中文强；换模型需重验（单模型 MVP 决策下的已知约束）

