# MCP 集成说明

## 使用的麦当劳 MCP Server

本项目使用麦当劳中国官方 MCP Server：

| 项目 | 说明 |
|------|------|
| Server URL | `https://mcp.mcd.cn` |
| Transport | Streamable HTTP |
| 认证方式 | Bearer Token |
| 工具总数 | 35 个 |
| SDK 版本 | MCP Python SDK 2.3.0 |

## 实际使用的 MCP 工具

本项目通过 `mcp_client.py` 联通全部 35 个麦当劳 MCP 工具，按业务场景分组如下：

### 用户信息类

| 工具名 | 用途 | 调用场景 |
|--------|------|----------|
| `query-my-account` | 查询用户积分账户 | Deal 组合时获取可用积分 |
| `query-my-coupons` | 查询已领优惠券 | Deal 组合时匹配优惠券 |
| `available-coupons` | 查询麦麦省可领券 | Deal 组合时匹配可领优惠券 |
| `now-time-info` | 当前时间信息 | Dashboard 聚合 |

### 门店查询类

| 工具名 | 用途 | 调用场景 |
|--------|------|----------|
| `query-nearby-stores` | 按城市/关键词查附近门店 | Deal 组合时自动定位门店 |
| `query-store-coupons` | 查门店可用优惠券 | Deal 组合时获取门店专属优惠 |
| `delivery-query-stores` | 外送门店查询 | 外送场景 |

### 菜单与点餐类

| 工具名 | 用途 | 调用场景 |
|--------|------|----------|
| `query-meals` | 查门店完整菜单 | Deal 组合时获取餐品列表 |
| `calculate-price` | 计算订单价格 | 订单价格预估 |
| `create-order` | 创建订单 | 确认 Deal 后下单 |
| `cancel-order` | 取消订单 | 用户取消 |
| `order-list` | 查历史订单 | Dashboard 展示 |

### 优惠活动类

| 工具名 | 用途 | 调用场景 |
|--------|------|----------|
| `campaign-calendar` | 营销活动日历 | 活动监控 + Dashboard |
| `auto-bind-coupons` | 一键领取麦麦省优惠券 | 自动领券 |
| `query-lottery-info` | 积分抽奖信息 | Dashboard 展示 |
| `draw-lottery` | 执行抽奖 | auto 模式自动抽奖 |

### 积分商城类

| 工具名 | 用途 | 调用场景 |
|--------|------|----------|
| `mall-points-products` | 积分商城商品列表 | Deal 组合时尝试积分兑换 |

### 营养类

| 工具名 | 用途 | 调用场景 |
|--------|------|----------|
| `list-nutrition-foods` | 餐品营养成分 | Deal 组合时营养搭配 |

### 主题活动类

| 工具名 | 用途 | 调用场景 |
|--------|------|----------|
| `query-party-city` | 主题活动城市列表 | 主题活动查询 |
| `query-party-store` | 主题活动门店列表 | 主题活动查询 |
| `query-partystore-date` | 主题活动日期 | 主题活动查询 |
| `query-partystore-session` | 主题活动场次 | 主题活动预约 |

## 调用流程

### Deal 组合主流程

```
用户自然语言输入
    │
    ▼
┌─────────────────────┐
│  OrderParser.parse() │  解析: 人数/预算/餐次/约束
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    ▼             ▼
┌─────────┐  ┌──────────────┐
│ Parse NL │  │ Fetch Stores │ query-nearby-stores
│ → Spec  │  │ → storeCode  │ (searchType=2, city, keyword, beType)
└────┬────┘  └──────┬───────┘
     │              │
     └──────┬───────┘
            ▼
┌─────────────────────────────────────────────┐
│            并行数据获取 (6 个 MCP 调用)         │
│  query-meals → 菜单 (121 items)               │
│  query-my-coupons → 用户优惠券                │
│  available-coupons → 麦麦省可领券              │
│  query-store-coupons → 门店优惠券              │
│  query-my-account → 积分账户                   │
│  mall-points-products → 积分商城               │
│  list-nutrition-foods → 营养数据               │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│           ResponseParser 解析                  │
│  Markdown + JSON 响应 → 结构化数据              │
│  (花括号计数法提取嵌入 JSON)                     │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│         _categorize_menu() 分类               │
│  combos / burgers / chicken / vegetarian     │
│  drinks / desserts / breakfast              │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│         _plan_allocation() 分配               │
│  按约束分组: vegetarian → 素食菜单             │
│              extra-burger → 汉堡菜单           │
│              剩余人数 → 最优套餐                │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│       _optimize_coupons() 优惠券优化           │
│  遍历所有组件，匹配最佳优惠券                   │
│  保存 original_price 用于节省计算              │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│       _optimize_points() 积分优化              │
│  查找积分商城可兑换商品                        │
│  替换最便宜的单点为积分免费商品                 │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│         _score_deal() 评估                    │
│  原价/最终价/节省/匹配度/缺口分析               │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│    DealReportGenerator → HTML 报告            │
│    包含: 需求/评估/组成表/价格分解/约束矩阵      │
└─────────────────────────────────────────────┘
```

### 活动监控流程

```
APScheduler 定时触发 (每4小时)
    │
    ├── campaign-calendar → 检查新活动
    ├── available-coupons → 检查新优惠券
    ├── query-lottery-info → 检查抽奖
    └── query-my-account → 检查积分到期
         │
         ▼
    通知用户 (或 auto 模式自动领取)
```

## MCP 调用参数要点

麦当劳 MCP 工具的 `inputSchema` 为空，实际参数在工具 `description` 中描述。关键参数规则：

| 工具 | 关键参数 | 说明 |
|------|----------|------|
| `query-nearby-stores` | `searchType=2, city, keyword, beType` | 不是 lat/lng，按城市+关键词搜索 |
| `query-meals` | `storeCode, beType, orderType` | 不是 storeId，beType=1到店/2外送/5得来速/6团餐 |
| `query-store-coupons` | `storeCode, orderType` | 不是 storeId |
| `create-order` | `storeCode, orderType, items[]` | items 含 productCode + quantity |

## 响应解析

麦当劳 MCP 返回 Markdown + 嵌入 JSON 格式（非纯 JSON），格式为：

```
# API Response Information
## Response Structure
- **data**: 字段描述
- ...
## Original Response
{"success":true,"code":200,"data":{...}}
```

本项目实现了 `ResponseParser` 使用**花括号计数法**（区分字符串内外）正确提取嵌入的 JSON，并解析为结构化数据：

- `parse_menu()` — 处理 `data.meals` 为 dict(code→details) + `data.categories[]` 分类
- `parse_stores()` — 处理 `data` 直接为门店列表数组
- `parse_coupons()` — 从 Markdown 分段解析优惠券
- `parse_account()` — 提取积分账户信息

## 业务价值

1. **降本增效** — 自动匹配最优套餐+优惠券+积分组合，典型场景节省 10-30%
2. **团餐场景** — 支持"10份饭，素食2份，汉堡加量4份"等复杂自然语言需求
3. **积分价值最大化** — 自动查找积分商城可兑换商品，替换低价单点
4. **活动感知** — 定时监控活动/优惠券/积分到期，主动提醒用户
5. **MCP 生态贡献** — 暴露 17 个增强工具作为本地 MCP Server，可被其他 AI 客户端调用
