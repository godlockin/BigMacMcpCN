# BigMacMcpCN - 麦当劳智能 Deal 组合助手

> 基于麦当劳 MCP 的自然语言 Deal 组合引擎：一句话描述需求，自动匹配最优套餐 + 单点 + 优惠券 + 积分兑换方案。

## 项目简介

BigMacMcpCN 是一个麦当劳个人助理，它能实时联通麦当劳 MCP Server 的 35 个工具端点，根据用户的自然语言需求（如"10份饭，素食2份，汉堡加量4份，预算500"），自动完成：

1. **需求解析** — 将自然语言解析为结构化订单（人数、预算、餐次、特殊要求）
2. **数据聚合** — 并行获取门店菜单、用户优惠券、可领优惠券、积分账户、积分商城、营养成分
3. **智能组合** — 按约束分组分配餐品，优先选用套餐（更划算），再补充单点
4. **优惠优化** — 自动匹配最佳优惠券，尝试用积分兑换免费商品
5. **方案评估** — 计算匹配度、价格分解、节省金额，生成可视化 HTML 报告

如果无法凑出完美方案，会输出**最接近的 Deal** 并标注缺口供用户 review。

## 核心能力

| 能力 | 说明 |
|------|------|
| NL 订单解析 | 支持中文自然语言："3个人吃午餐预算200，素食1份，不要辣" |
| Deal 组合引擎 | 套餐 + 单点 + 优惠券 + 积分，四维组合优化 |
| 实时 MCP 联通 | 35 个麦当劳 MCP 工具全部联通验证 |
| 活动监控 | APScheduler 定时监控活动、优惠券、积分到期 |
| 可视化报告 | 自动生成 HTML Deal 方案报告（含价格分解、约束满足度矩阵） |
| 本地 MCP Server | 暴露 17 个增强工具，可被 Cursor / WorkBuddy 等 MCP 客户端调用 |

## 快速开始

### 前置条件

- Python 3.11+
- 麦当劳 MCP Token（[申请方式](https://github.com/M-China/mcd-mcp-server)）

### 安装

```bash
git clone https://github.com/godlockin/BigMacMcpCN.git
cd BigMacMcpCN

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 配置

```bash
# 复制配置模板
cp config.example.json config.json

# 编辑 config.json，填入你的 MCP Token
# 或者使用环境变量
export MCD_MCP_TOKEN="your_token_here"
```

配置文件示例（`config.json`）：

```json
{
  "mcp_server_url": "https://mcp.mcd.cn",
  "mcp_token": "${MCD_MCP_TOKEN}",
  "monitor_interval_minutes": 240,
  "auto_mode": false,
  "default_location": {
    "city": "北京",
    "keyword": "朝阳区",
    "be_type": 1
  }
}
```

### 运行 Demo

```bash
# 端到端 Deal 组合 Demo
python demo.py "我今天中午要组织一个workshop，要准备10份饭，其中素食2份，汉堡加量4份，预算500"
```

输出示例：

```
[1/5] 连接麦当劳 MCP Server...
      ✅ 已连接，35 个工具可用

[2/5] 解析自然语言需求...
      人数: 10
      预算: ¥500.0
      餐次: lunch
      要求: ['vegetarian:2', 'extra-burger:4']

[3/5] 获取门店菜单、优惠券、积分、商城数据...

[4/5] 组合最优 Deal (套餐+单点+优惠券+积分)...
      ✅ Deal 组合完成

[5/5] 生成方案报告...
=== 完整 Deal 方案 (10人) ===
匹配度: 100%
应付总价: ¥226.70 | 人均: ¥22.67
```

### 运行 CLI 交互模式

```bash
python run.py
```

支持的命令：`dashboard`、`nearby`、`menu`、`recommend`、`order`、`deals`、`claim`、`monitor`、`auto`

### 运行本地 MCP Server

```bash
python run_mcp_server.py
```

可在 WorkBuddy / Cursor 等 MCP 客户端中配置：

```json
{
  "mcpServers": {
    "mcd-deal-composer": {
      "command": "python3",
      "args": ["/path/to/run_mcp_server.py"]
    }
  }
}
```

## 项目结构

```
BigMacMcpCN/
├── README.md                    # 项目介绍
├── CONTEST_DECLARATION.md       # 参赛声明（官方文件，不可修改）
├── MCP_INTEGRATION.md           # MCP 集成说明
├── mcp-config.example.json      # 脱敏 MCP 配置示例
├── workbuddy.md                 # WorkBuddy 开发上下文
├── requirements.txt             # Python 依赖
├── config.example.json          # 应用配置模板
├── .gitignore
├── demo.py                      # 端到端 Demo
├── run.py                       # CLI 入口
├── run_mcp_server.py            # MCP Server 入口
├── mcd_assistant/
│   ├── __init__.py
│   ├── config.py                # 配置加载
│   ├── models.py                # 数据模型
│   ├── mcp_client.py            # 麦当劳 MCP 客户端
│   ├── mcp_server.py            # 本地增强 MCP Server (17 tools)
│   ├── order_parser.py          # 自然语言订单解析器
│   ├── response_parser.py       # MCP 响应解析器
│   ├── deal_composer.py         # Deal 组合引擎（核心）
│   ├── deal_report.py           # HTML 报告生成器
│   ├── recommender.py           # 智能推荐引擎
│   ├── monitor.py               # 活动监控
│   └── notifier.py              # 通知系统
├── tests/
│   └── test_order_parser.py     # 单元测试
└── data/                        # 生成的报告和数据
```

## 目标用户

- **团餐组织者** — 需要 workshop / 团建 / 会议餐食，有特殊要求（素食、加量、不辣）
- **预算敏感用户** — 想在预算内获得最优组合，自动应用优惠券和积分
- **麦门忠实用户** — 关注积分、活动、优惠券，想最大化积分价值
- **AI 开发者** — 作为 MCP Skill 开发参考，学习如何编排 MCP 工具链

## 使用 WorkBuddy 开发

本项目使用 WorkBuddy AI 进行开发。WorkBuddy 提供了 MCP 连接器配置、代码编写、调试全流程支持。开发对话上下文见 `workbuddy.md`。

## 技术栈

- **MCP SDK** 2.3.0 — Streamable HTTP transport
- **Python** 3.11+ — async/await
- **APScheduler** — 后台活动监控
- **Rich** — 终端 UI
- **Dataclasses** — 类型安全的数据模型

## License

MIT License - 详见 [LICENSE](LICENSE)

## 致谢

- [麦当劳 MCP Server](https://github.com/M-China/mcd-mcp-server) — 提供 35 个 API 工具
- [WorkBuddy AI](https://www.workbuddy.cn) — 官方开发工具合作伙伴
