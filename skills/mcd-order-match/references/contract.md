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

`requirements` 数量是食品份数，每个食品单位最多分配给一个需求。指定整桶／套餐用 `product_requirements`（结构同食品需求，any_of 使用 prepare 返回的顶层 product_code）；整套保留给指定人，其本人食品需求可校验套内组成，不重复算作额外购买。any_of 表示替代，不要求全部选中。未给预算可设 null；participants 是完整实际人物名单，assumptions 记录默认理解。

校验不能判断宿主是否漏读原话，source_quote 只是可追溯依据。宿主应核对完整口述的硬条件，尤其价格、人数、否定、过敏和后续修正。需求修正包括补充澄清的原话，expected_revision 必须与 match-state 一致。

`mcd-match-plan` 返回 quoted_candidates、conditional_candidates 或 no_verified_candidate。现金单位分；coupon_count_submitted 为报价请求券数，非核销数。membership_applied=unknown、activity_count=null 表示没有归因证据，points_spent=0。preferences_pending 由宿主审查；memory_fit_score 是本机偏好与反馈的启发式软分，不是满意概率。

后续用 match-state 的 revision 提交完整新 intent；地点／时间改变重取 context。match-memory facts 使用 person/key/value/source/duration/source_quote/confidence，引用当前完整口述。match-conversation new/resume 只恢复语义，历史报价不可执行。

用户选中后 match-select；明确下单后 match-create 传 context_id、plan_id、authorized=true、稳定 request_key、max_cash_cents 和最新报价的 take_way_code。未知结果禁止换键重试，match-order-status 用 request_key 查询。match-feedback 支持 rejected/satisfied/disliked/cancelled，不能证明支付。

天气只在明确坐标时查询，失败 status=unknown；不能省略未知提示。距离基于位置关键词，非设备定位。报价时间/预约时间、营业时段及未知预约可用性都应向用户说明。
