#!/usr/bin/env python3
"""Independent local live-account workbench; only queries and price calculation."""
import argparse
import getpass
import importlib.util
import logging
import os
from pathlib import Path

from mcd_assistant.live import LiveWorkbench, OfficialTransport
from mcd_assistant.local_http import make_http_server
from mcd_assistant.matching import Matcher
from mcd_assistant.preference_memory import PreferenceMemory, default_memory_path
from mcd_assistant.weather import Weather


def main() -> int:
    parser = argparse.ArgumentParser(description='Mac 本机点餐模拟器：真实菜单、位置搜索、方案比较与核价')
    parser.add_argument('--port', type=int, default=8789)
    parser.add_argument('--token-stdin', action='store_true', help='从终端隐藏输入 Token，不保存到磁盘')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port requires 0..65535')
    if importlib.util.find_spec('mcp') is None:
        parser.error('缺少 MCP SDK，请先安装 requirements.txt')
    logging.disable(logging.CRITICAL)
    token = getpass.getpass('MCP Token（隐藏输入）：') if args.token_stdin else os.environ.get('MCD_MCP_TOKEN', '')
    state = LiveWorkbench(OfficialTransport(), token)
    matcher = Matcher(state, Weather(), memory=PreferenceMemory(default_memory_path()))
    def dispatch(action: str, payload: object) -> object:
        if action.startswith('match-'):
            return matcher.dispatch(action, payload)
        if action in {'connect', 'logout', 'stores'}:
            matcher.clear()
        return state.dispatch(action, payload)
    token = ''
    server = make_http_server(Path(__file__).parent / 'web' / 'simulator.html',
                              {'city': '上海', 'keyword': '七宝地铁站'}, dispatch, args.port)
    print(f'本机点餐模拟器 http://127.0.0.1:{server.server_port} （Ctrl+C 退出）', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.dispatch('logout', {})
        matcher.memory.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
