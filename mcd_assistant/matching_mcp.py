"""Standard MCP interface; no model/provider API required."""
import asyncio
import json
import os

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from .decision import InputError
from .live import LiveError, LiveWorkbench, OfficialTransport
from .matching import Intent, Matcher
from .preference_memory import PreferenceMemory, PreferenceFact, default_memory_path
from .weather import Weather


def tools_list() -> list[types.Tool]:
    def tool(name: str, description: str, properties: dict, required: list[str]) -> types.Tool:
        return types.Tool(name='mcd-' + name, description=description,
                          inputSchema={'type': 'object', 'properties': properties, 'required': required,
                                       'additionalProperties': False},
                          annotations=types.ToolAnnotations(read_only_hint=name in {'match-menu', 'match-recheck', 'match-state'},
                                                           destructive_hint=name in {'match-memory', 'match-create'}))
    string = {'type': 'string'}
    context = {'context_id': string}
    store = {**context, 'store_code': string}
    return [
        tool('match-context', '读取本人积分/券、附近营业门店、目标北京时间和可选坐标天气；清空上一轮撮合。只读，不登录手机账号。', {
            'city': string, 'keyword': string, 'target_time': {'type': 'string', 'description': 'ISO 8601 北京时间，省略为即时'},
            'max_distance_m': {'type': 'integer', 'minimum': 0, 'maximum': 50000},
            'max_walking_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 240},
            'weather': {'type': 'object', 'properties': {'latitude': {'type': 'number'}, 'longitude': {'type': 'number'}},
                        'additionalProperties': False}}, ['city', 'keyword']),
        tool('match-menu', '读取指定候选门店在目标时间的真实菜单。菜单优惠仅参考，随单购不等于已持卡。', store, ['context_id', 'store_code']),
        tool('match-prepare', '准备宿主选出的 1–8 个商品：真实套餐选配、单点、门店可用券别名；不给模型券码，不买卡。', {
            **store, 'product_codes': {'type': 'array', 'items': string, 'minItems': 1, 'maxItems': 8},
            'preferred_terms': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 64}, 'maxItems': 16}}, ['context_id', 'store_code', 'product_codes']),
        tool('match-intent', '宿主模型提交口述理解后的完整需求；校验原文引用、真实子项编码和版本。冲突/忌口证据不足放 unresolved。后续修正提交新完整状态。', {
            **context, 'expected_revision': {'type': 'integer', 'minimum': 0}, 'intent': Intent.model_json_schema()}, ['context_id', 'expected_revision', 'intent']),
        tool('match-plan', '动态组合套餐/单点/券，按真实子项数量匹配每人需求、限制券复用，官方整单核价后推荐 A/B；有界搜索不证明全局最优。', {
            **context, 'revision': {'type': 'integer', 'minimum': 1},
            'max_quotes': {'type': 'integer', 'minimum': 1, 'maximum': 24},
            'max_nodes': {'type': 'integer', 'minimum': 1, 'maximum': 100000}}, ['context_id', 'revision']),
        tool('match-recheck', '重新核价已撮合方案，报告价格变化与预算状态；不创建订单、不核销券。', {
            **context, 'plan_id': string}, ['context_id', 'plan_id']),
        tool('match-state', '恢复需求、上轮方案、偏好、当前版本。历史价格不可执行，须重新准备与核价。', {}, []),
        tool('match-memory', '本人账号隔离的本地偏好。引用当前口述；推断不得生成饮食硬限制。可读取、记录、删除或清除。', {
            'operation': {'enum': ['read', 'remember', 'forget', 'clear']},
            'facts': {'type': 'array', 'items': PreferenceFact.model_json_schema(), 'maxItems': 32},
            'fact_ids': {'type': 'array', 'items': string, 'maxItems': 32}}, []),
        tool('match-conversation', '开启或恢复本账号会话；保留语义与版本，历史报价必须重核。', {
            'operation': {'enum': ['read', 'new', 'resume']}, 'conversation_id': string}, []),
        tool('match-feedback', '用户明确反馈方案：拒绝不等于不喜欢全部餐品；满意不等于官方付款。', {
            'plan_id': string, 'outcome': {'enum': ['rejected', 'satisfied', 'disliked', 'cancelled']},
            'reason': {'type': 'string', 'maxLength': 500}}, ['plan_id', 'outcome']),
        tool('match-select', '记录用户选中的当前方案；不是创建、付款或满意。', {
            **context, 'plan_id': string}, ['context_id', 'plan_id']),
        tool('match-create', '用户明确下单授权后重核价并创建自取订单；金额上限、当前选择、硬条件和请求去重校验。只创建不付款，未知结果不重试。', {
            **context, 'plan_id': string, 'authorized': {'type': 'boolean'}, 'request_key': {'type': 'string', 'maxLength': 128},
            'max_cash_cents': {'type': 'integer', 'minimum': 0, 'maximum': 1000000}, 'take_way_code': string},
             ['context_id', 'plan_id', 'authorized', 'request_key', 'max_cash_cents', 'take_way_code']),
        tool('match-order-status', '查询当前账号本地创建记录对应的官方订单；不接受模型臆造付款事实。', {
            'request_key': string}, ['request_key']),
    ]


class MatchingServer:
    def __init__(self, matcher: Matcher | None = None):
        self.matcher = matcher or Matcher(LiveWorkbench(OfficialTransport(), os.environ.get('MCD_MCP_TOKEN', '')), Weather(),
                                          memory=PreferenceMemory(default_memory_path()))
        self.guard = asyncio.Lock()

        async def list_tools(context: object, params: object) -> types.ListToolsResult:
            return types.ListToolsResult(tools=tools_list())

        async def call_tool(context: object, params: types.CallToolRequestParams) -> types.CallToolResult:
            if params.name not in {t.name for t in tools_list()}:
                return types.CallToolResult(is_error=True, content=[types.TextContent(type='text', text='Unknown matching tool')])
            try:
                async with self.guard:
                    result = await asyncio.to_thread(self.matcher.dispatch, params.name.removeprefix('mcd-'), params.arguments or {})
                return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                                            structured_content=result)
            except ValidationError:
                message = '需求结构无效：检查类型、范围、原文引用、人数和 ID；未回显个人口述。'
            except (InputError, LiveError) as exc:
                message = str(exc)
            except Exception:
                message = '撮合失败，请检查网络和参数；未记录凭据或口述原文。'
            return types.CallToolResult(is_error=True, content=[types.TextContent(type='text', text=message)])

        self.server = Server('mcd-order-matcher', version='4.0.0', on_list_tools=list_tools, on_call_tool=call_tool)

    async def run(self) -> None:
        try:
            async with stdio_server() as (read, write):
                await self.server.run(read, write, self.server.create_initialization_options())
        finally:
            self.matcher.memory.close()
