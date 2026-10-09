# BigMacMcpCN · 团餐变更决策台

**新增 Mac 本机真实点餐模拟器**：独立网页界面，连接本人 Token，查看积分与卡包、搜索真实门店、读取默认套餐组成、准备核价候选、重算人员变更，并进行官方整单核价。当前接口未提供麦金卡持卡状态，显示无法确认。双击 `start_simulator.command`，或运行 `python3 simulator.py --token-stdin`。完整使用和实测边界见 [LIVE_WORKBENCH.md](LIVE_WORKBENCH.md)。离线演示仍可按下面方式无 Token 使用。

**有人变卦，不必整桌重来。**

10 人 workshop 配好餐之后，一人退出、一人改成不辣，或者优惠券失效：保留已认可的餐品，计算最少影响几个人、需要换哪些餐、现金如何变化。面向会议组织者、团餐发起人和需要比较调整代价的个人用户。

v2 新增确定性求解器、浏览器交互台、命令行和本地 MCP 工具。**离线决策链路不联网、不下单、不领券、不兑换。** 无 Token 即可体验。实时工作台补充主动查询和默认餐适配，仍须官方整单核价。

## 30 秒体验

Python 3.11+；新决策台只用标准库，不必安装旧界面的依赖。

```bash
git clone https://github.com/godlockin/BigMacMcpCN.git
cd BigMacMcpCN
python3 decision.py serve --demo
```

打开 **http://127.0.0.1:8788**，点击「演示：一人退出＋一人不辣」。页面显示旧方案、新方案、受影响人数、金额变化和预算取舍。可逐人勾选锁定、修改预算、模拟优惠券失效，下载决策 JSON。

> `examples/workshop.json` 是 10 人**合成数据**，餐品、价格、属性均非麦当劳实时数据。页面持续显示来源，不将演示结果冒充实际节省。

```bash
python3 decision.py plan --demo --output decision-result.json
python3 decision.py plan --input my-request.json
python3 decision.py serve --input my-request.json --port 8789
```

CLI 无可行方案返回 2，输入错误返回 1。有方案但搜索达到上限时为 `feasible`，不是已证明最优；应读取 JSON 状态。

## 差异化能力

| 问题 | v2 的处理 |
|---|---|
| 有人退出，别人已经选好了 | 移除退出者，优先保留其他人的餐；退出者不计入受影响人数 |
| 一个人临时改要求 | 基于每人的允许候选与已确认标签重新分配；未知属性不视为满足 |
| 已确认的餐不能改 | `locked_people` 锁定餐品组成；冲突时明确无解，不静默解锁 |
| 同一餐的券失效了 | 可转为相同餐品的无券候选；显示权益／价格变化，不误算为改餐 |
| 预算不足，究竟差多少 | 完整搜索后给出满足其他条件的最低预算与缺口 |
| 多花一点能少改几个人？ | 输出各个受影响人数对应的最低现金支出 |
| 券或积分被重复使用 | 共享资源容量与总积分余额约束参与搜索 |
| 搜索太大 | 明确返回截断状态，不将未找到说成无解 |

目标按字典序排列：**受影响人数 → 餐品增删件数 → 现金支出 → 积分消耗**。换一个商品记为一次移除和一次加入，共 2 件改动。预算、饮食、锁定和资源条件均先作为硬约束。

## 输入契约与边界

完整格式见 [examples/workshop.json](examples/workshop.json)。

- `snapshot`：来源 `synthetic` 或 `user_snapshot`、时间、门店、完整餐候选、资源容量、积分余额。
- 候选：唯一 `id`、名称、**整数分**价格、餐品类型列表 `items`、已确认属性 `verified_tags`、积分消耗和资源 ID。重复资源 ID 表示消耗多个单位。
- `participants`：稳定个人 ID、名字、允许候选、必需属性。每人只分配一个**完整餐候选**；套餐内部组成须提前核实。
- `previous_assignments`：上次结果的 `assignments` 原样传入，保留原价格和餐品组成。退出者从当前参与者移除。
- `locked_people`：必须同时存在于旧、新参与者中。保护餐品组成，不保证旧价格或旧券继续有效。
- `budget_cents`：总现金预算。营养、优惠有效期和可售时段须由快照准备方核实后筛选。

范围：1–16 人，最多 64 个候选，默认 200,000 个搜索节点（可配置至 500,000）。只在提供的候选与资源规则内计算；**不自动枚举官方全菜单、不拆分共享套餐、不推断券叠加、不证明全市场最低价**。整数分合计不是最终支付金额。

| 状态 | 含义 |
|---|---|
| `optimal` | 搜索完成，候选空间内按既定目标最优 |
| `feasible` | 有可行方案，但搜索被截断 |
| `infeasible` | 搜索完成或个人候选直接冲突，给定条件无解 |
| `unknown` | 搜索被截断且尚无预算内方案 |

只有搜索完成才提供 `minimum_budget_cents` / `minimum_extra_budget_cents`；截断时仅提供已找到的金额，并明确未证明。

## 接入 MCP 与自测

详见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)。新工具 **`mcd-decision-plan`** 可独立离线调用，不要求麦当劳 Token。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
python3 -m pytest -q
python3 run_mcp_server.py
```

测试覆盖原解析、历史反例、10 人变更、锁定冲突、券失效重定价、积分／共享资源、输入校验、截断语义、80 组实例与独立穷举对照、本地 HTTP 与 MCP stdio。MCP 测试未安装依赖时会显式跳过。见 [VALIDATION.md](VALIDATION.md)。

真实远程工具需配置本人 `MCD_MCP_TOKEN` 并遵守服务规则。不要把 Token 放入对话、快照或公开仓库。新网页只监听回环地址，无外部资源、无遥测，也不保存输入快照。

## 原版本迁移

v1 自然语言 Deal 仍是启发式草案。v2 修复轮流选贵套餐、数量计数和积分重复统计；不再凭名称断言素食／不辣，或把同类商品优惠券直接套用。

旧解析器没有证据证明的券适用范围和积分兑换关系，默认不应用。`verified_*` 是内部确认标记，**不是官方响应字段的声明**。复杂个人约束使用新工具；旧报告“规则通过率”不是成功下单概率。

旧 `run.py` 的监控与消耗类操作不属于本次新决策链路，新演示不启动这些路径。未核验自动化调用授权，不建议把旧监控作为默认演示入口。

```text
decision.py                   # CLI / 本机 HTTP 服务
web/decision.html             # 浏览器变更工作台
mcd_assistant/decision.py     # 纯函数求解器
examples/workshop.json        # 合成场景
tests/                        # 行为、回归、穷举和接口测试
```

独立参赛作品，非麦当劳官方产品。保留官方原版 [参赛声明](CONTEST_DECLARATION.md)。v2 由 Codex 升级；[workbuddy.md](workbuddy.md) 仅记录 v1 作者提供的开发背景，不声称本次工作由 WorkBuddy 完成。
