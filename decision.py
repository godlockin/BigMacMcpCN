#!/usr/bin/env python3
"""Offline decision CLI and loopback-only interactive demonstration."""
import argparse
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcd_assistant.decision import InputError, solve

ROOT = Path(__file__).resolve().parent


def read_request(path: Path) -> object:
    if path.stat().st_size > 262_144:
        raise InputError("input exceeds 256 KiB")
    return json.loads(path.read_text(encoding="utf-8"))


def make_server(request: object, port: int = 8788) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)
    page = (ROOT / "web" / "decision.html").read_text(encoding="utf-8").replace("__SESSION_TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body: bytes, mime: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json_reply(self, status: int, value: object) -> None:
            self.reply(status, json.dumps(value, ensure_ascii=False).encode())

        def do_GET(self) -> None:
            if self.path == "/":
                self.reply(200, page.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/input" and secrets.compare_digest(self.headers.get("X-Decision-Token", ""), token):
                self.json_reply(200, request)
            else:
                self.json_reply(404, {"error": "Not found"})

        def do_POST(self) -> None:
            if self.path != "/api/solve":
                self.json_reply(404, {"error": "Not found"})
                return
            if not secrets.compare_digest(self.headers.get("X-Decision-Token", ""), token):
                self.json_reply(403, {"error": "Invalid session token"})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.json_reply(415, {"error": "Use application/json"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 262_144:
                    raise InputError("request requires 1..262144 bytes")
                self.connection.settimeout(10)
                data = json.loads(self.rfile.read(length))
                self.json_reply(200, solve(data))
            except (InputError, ValueError, UnicodeError) as exc:
                self.json_reply(400, {"error": str(exc)})
            except TimeoutError:
                self.json_reply(408, {"error": "Request timed out"})

        def log_message(self, format_string: str, *args: object) -> None:
            # Never log request bodies, addresses, coupon IDs or tokens.
            sys.stderr.write(json.dumps({"event": "decision_http", "method": self.command,
                                         "status": str(args[1]) if len(args) > 1 else "unknown"}) + "\n")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


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
