# WorkBuddy 开发上下文

本项目使用 WorkBuddy AI 进行开发。以下是开发过程中的关键对话上下文摘要，用于核验 WorkBuddy 联动活动奖励条件。

## 开发环境

- **开发工具**: WorkBuddy AI (https://www.workbuddy.cn)
- **MCP 连接器**: mcd-mcp (streamablehttp, https://mcp.mcd.cn)
- **开发语言**: Python 3.13
- **MCP SDK**: mcp 2.3.0

## 开发流程摘要

### 阶段 1：项目研究

通过 WorkBuddy 连接麦当劳 MCP Server，发现 35 个工具端点，分析了工具矩阵和业务场景。

### 阶段 2：MCP 客户端开发

使用 WorkBuddy 编写 `mcp_client.py`，实现：
- Streamable HTTP transport 连接 (create_mcp_http_client + streamable_http_client)
- Bearer Token 认证
- 全部 35 个工具的封装调用
- 限流保护和错误处理

### 阶段 3：Deal 组合引擎

使用 WorkBuddy 开发核心 Deal 组合逻辑：
- `order_parser.py` — 自然语言解析（中文 regex 模式匹配）
- `response_parser.py` — Markdown+JSON 响应解析（花括号计数法）
- `deal_composer.py` — 四维组合优化（套餐+单点+优惠券+积分）
- `deal_report.py` — HTML 可视化报告生成

### 阶段 4：端到端验证

在 WorkBuddy 中运行端到端 Demo，验证真实 MCP 数据联通：
- 输入: "我今天中午要组织一个workshop，要准备10份饭，其中素食2份，汉堡加量4份，预算500"
- 结果: 100% 匹配度，10 个套餐，2 张优惠券，总价 ¥226.70，人均 ¥22.67
- 输出: HTML 报告 + JSON 数据

## WorkBuddy 联动说明

- 本项目在开发过程中真实使用了 WorkBuddy AI 的对话式编程能力
- WorkBuddy 配置了麦当劳 MCP 连接器用于工具调用验证
- 使用 WorkBuddy 的文件读写能力进行代码编辑和调试
- 本文件 `workbuddy.md` 作为 WorkBuddy 联动活动奖励的核验凭证
