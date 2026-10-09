"""Shared loopback-only, session-authenticated local UI transport."""
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Callable
from pydantic import ValidationError

from .decision import InputError
from .live import LiveError


def make_http_server(page_path: Path, initial: object, dispatch: Callable[[str, object], object], port: int) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)
    page = page_path.read_text(encoding='utf-8').replace('__SESSION_TOKEN__', token).encode()
    guard = Lock()

    class Handler(BaseHTTPRequestHandler):
        def valid_host(self) -> bool:
            return self.headers.get('Host', '') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def reply(self, status: int, value: object, html: bool = False) -> None:
            body = page if html else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def authenticated(self) -> bool:
            return self.valid_host() and secrets.compare_digest(self.headers.get('X-Decision-Token', ''), token)

        def do_GET(self) -> None:
            if not self.valid_host():
                self.reply(403, {'error': 'Invalid host'})
            elif self.path == '/':
                self.reply(200, None, html=True)
            elif self.path == '/api/input' and self.authenticated():
                self.reply(200, initial)
            else:
                self.reply(404, {'error': 'Not found'})

        def do_POST(self) -> None:
            if not self.authenticated():
                self.reply(403, {'error': 'Invalid session'})
                return
            if not self.path.startswith('/api/') or '/' in self.path[5:]:
                self.reply(404, {'error': 'Not found'})
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.reply(415, {'error': 'Use application/json'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 262_144:
                    raise InputError('request requires 1..262144 bytes')
                self.connection.settimeout(10)
                payload = json.loads(self.rfile.read(length))
                with guard:
                    self.reply(200, dispatch(self.path[5:], payload))
            except ValidationError:
                self.reply(400, {'error': '需求结构无效：检查原文引用、人数、类型和范围。未回显个人口述。'})
            except (InputError, ValueError, UnicodeError) as exc:
                self.reply(400, {'error': str(exc)})
            except LiveError as exc:
                self.reply(502, {'error': str(exc)})
            except TimeoutError:
                self.reply(408, {'error': 'Request timed out'})
            except Exception:
                self.reply(500, {'error': 'Internal error; no credentials logged'})

        def log_message(self, format_string: str, *args: object) -> None:
            sys.stderr.write(json.dumps({'event': 'local_http', 'method': self.command,
                                         'status': str(args[1]) if len(args) > 1 else 'unknown'}) + '\n')

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)
