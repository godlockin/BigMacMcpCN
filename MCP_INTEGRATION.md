# MCP 集成与决策证据边界

## 服务与工具

麦当劳中国远程服务：`https://mcp.mcd.cn`，Streamable HTTP，用户本人 Bearer Token。实际工具与参数以当前服务发现为准，不以固定工具数量证明兼容性。

本地服务：`python3 run_mcp_server.py`（stdio）。新工具 **`mcd-decision-plan`** 纯本地执行，不连接远程服务；旧增强工具通过 `mcd_assistant/mcp_client.py` 调用远程能力。

新工具输入与 [examples/workshop.json](examples/workshop.json) 相同，必填 `snapshot`、`participants`、`budget_cents`。重新规划时将旧结果 `assignments` 放入 `previous_assignments`，调整当前参与者／预算／资源，设置 `locked_people`。

输出是可审查的计划，不是订单：逐人餐品、`changes`、退出者、估算金额、积分、资源用量、预算门槛和搜索完成状态。schema 与边界见 [README](README.md#输入契约与边界)。

## 真实数据流程

1. 用户主动通过客户端查询 `query-nearby-stores`、`query-meals`、`query-meal-detail`，确认门店和完整餐候选。
2. 需要权益时查询 `query-store-coupons`、`query-my-coupons`、`query-my-account`、`mall-product-detail`，确认权益实例、次数、有效期、商品范围和额外现金。查询不等于领券或兑换。
3. 客户端／用户将有证据的信息整理为规范化快照，设置 `source=user_snapshot`。无依据的饮食标签不得加入 `verified_tags`。完整餐和权益互斥规则由准备方确认；**本次未实现对全部官方响应的自动归一化**。
4. `mcd-decision-plan` 本地求解与比较。修改预算可复用快照，失效或条件变化时需刷新数据。
5. 选定方案后，通过 `calculate-price` 对实际门店、就餐方式、商品结构和券参数做**整单核价**。本次升级未自动执行此步骤；候选金额相加不等于官方报价。
6. 下单、付款、领券、兑换是另一次明确授权操作，不由新工具触发。

## 验证边界

- 本次验证：合成快照计算、锁定与资源约束、变更比较、截断语义、CLI、本地 HTTP、MCP 离线调用，详见 VALIDATION.md。
- 本次未验证：真实 Token 联调、官方参数自动归一化、全菜单最优、优惠叠加、支付或订单履约。
- v1 历史“35 个工具全通／100%匹配／节省10–30%”不能作为 v2 验收结果。本文件取代旧集成说明中的这些保证。

业务价值：保留已确认选择，解释谁受影响、为何调整、预算缺口，而非增加自动交易次数。

真实服务使用须遵守 MCP 服务规则和竞赛规则；公开参赛不代表商业使用或任意自动化调用授权。
