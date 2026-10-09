# 宿主与工具契约

安装：支持 Skill 的客户端加载 `skills/mcd-order-match`；支持标准 MCP stdio 的客户端配置 `run_matching_server.py`。没有 Skill 自动加载机制的客户端，可把 SKILL.md 放入它的项目指令。没有麦当劳账号 Token 时只可读取 match-state，业务查询需要本人 Token。无需模型 API Key。

工具返回 context_id，后续工具沿用；门店和子项编码必须从同一上下文查询。上下文 10 分钟过期；更换账号、地点或时间后旧上下文不可用。每个 MCP 进程服务一个用户，不部署为共享账号服务器。

`mcd-match-intent` 示例（编码必须替换为真实 prepare 返回的子项编码）：

```json
{
  "context_id": "来自 context 的 ID",
  "expected_revision": 0,
  "intent": {
    "transcript": "三个人，不，小王不来了，两个人，各一个汉堡和一杯饮料，预算八十，少走路",
    "summary": "两人，每人汉堡和饮料，80 元内，少走路优先",
    "people": 2,
    "budget_cents": 8000,
    "priority": "travel",
    "requirements": [
      {"id":"p1-main","person":"p1","description":"汉堡","source_quote":"各一个汉堡","any_of":["真实汉堡子项编码"],"quantity":1},
      {"id":"p1-drink","person":"p1","description":"饮料","source_quote":"一杯饮料","any_of":["真实饮料子项编码"],"quantity":1},
      {"id":"p2-main","person":"p2","description":"汉堡","source_quote":"各一个汉堡","any_of":["真实汉堡子项编码"],"quantity":1},
      {"id":"p2-drink","person":"p2","description":"饮料","source_quote":"一杯饮料","any_of":["真实饮料子项编码"],"quantity":1}
    ],
    "excluded_codes": [],
    "unresolved": [],
    "preferences": [],
    "corrections": ["三人修正为两人，小王退出"]
  }
}
```

`requirements` 的数量是食品份数，不是食用人数；每个食品单位最多分配给一个需求。不要同时提交“套餐一个”和其展开子项作为双重需求。any_of 同一列表允许替代，不要求全部选中。

校验不能判断宿主是否漏读原话，source_quote 只是可追溯依据。宿主应核对完整口述的硬条件，尤其价格、人数、否定、过敏和后续修正。需求修正包括补充澄清的原话，expected_revision 必须与 match-state 一致。

`mcd-match-plan` 返回 quoted_candidates 或 no_verified_candidate/needs_clarification。现金单位分；coupon_count_submitted 是提交报价请求的券数，非核销数。membership_applied=unknown、activity_count=null 表示没有归因证据，points_spent=0。preferences_pending 原样传回：软偏好由宿主审查，求解器未量化其效用。

天气只在明确坐标时查询，失败 status=unknown；不能省略未知提示。距离基于位置关键词，非设备定位。报价时间/预约时间、营业时段及未知预约可用性都应向用户说明。
