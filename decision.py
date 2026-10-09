#!/usr/bin/env python3
"""Offline decision CLI and loopback-only interactive demonstration."""
import argparse
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

from mcd_assistant.decision import InputError, solve
from mcd_assistant.local_http import make_http_server

ROOT = Path(__file__).resolve().parent


def read_request(path: Path) -> object:
    if path.stat().st_size > 262_144:
        raise InputError("input exceeds 256 KiB")
    return json.loads(path.read_text(encoding="utf-8"))


def make_server(request: object, port: int = 8788) -> ThreadingHTTPServer:
    def dispatch(action: str, payload: object) -> object:
        if action != "solve":
            raise InputError("Unknown action")
        return solve(payload)
    return make_http_server(ROOT / "web" / "decision.html", request, dispatch, port)


def main() -> int:
    parser = argparse.ArgumentParser(description="团餐变更决策台：离线、只读、下单前规划")
    parser.add_argument("command", choices=["plan", "serve"])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="使用明确标注的合成数据")
    source.add_argument("--input", type=Path, help="规范化 JSON 快照与需求")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--output", type=Path, help="保存 plan JSON；不含 Token")
    args = parser.parse_args()
    try:
        request = read_request(ROOT / "examples" / "workshop.json" if args.demo else args.input)
        result = solve(request)  # Validate input before opening the local server.
        if args.command == "plan":
            content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                args.output.write_text(content, encoding="utf-8")
            else:
                print(content, end="")
            return 0 if result["assignments"] else 2
        if not 0 <= args.port <= 65535:
            raise InputError("port must be 0..65535")
        server = make_server(request, args.port)
        print(f"团餐变更决策台 http://127.0.0.1:{server.server_port} （只读；Ctrl+C 退出）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except (InputError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
