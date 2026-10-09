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
from .weather import Weather


def tools_list() -> list[types.Tool]:
    def tool(name: str, description: str, properties: dict, required: list[str]) -> types.Tool:
        return types.Tool(name='mcd-' + name, description=description,
                          inputSchema={'type': 'object', 'properties': properties, 'required': required,
                                       'additionalProperties': False},
                          annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False))
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
        tool('match-state', '读取本进程当前上下文、需求版本和已准备餐品；不跨会话持久化个人信息。', {}, []),
    ]


class MatchingServer:
    def __init__(self, matcher: Matcher | None = None):
        self.matcher = matcher or Matcher(LiveWorkbench(OfficialTransport(), os.environ.get('MCD_MCP_TOKEN', '')), Weather())
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

        self.server = Server('mcd-order-matcher', version='3.0.0', on_list_tools=list_tools, on_call_tool=call_tool)

    async def run(self) -> None:
        async with stdio_server() as (read, write):
            await self.server.run(read, write, self.server.create_initialization_options())
