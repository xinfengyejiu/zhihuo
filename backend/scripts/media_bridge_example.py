"""自媒体一键发布 · 本机桥接示例(小红书 / 抖音 / 头条)。

织火不会替你登录小红书私有接口。正确用法是:

1. 在本机(或你自己的服务器)跑这份桥接服务;
2. 在织火「自媒体 → 账号 → 发布凭证」里填写:
     发布 API 地址: http://127.0.0.1:8765
     Access Token:  与下面 BRIDGE_TOKEN 一致(任意自定密钥)
3. 成稿页点「一键发布」→ 织火 POST 到本服务的 /publish。

你在下面 publish_handler 里接自己的正式能力即可
(官方开放平台、已获授权的企业网关、RPA 等)。
本示例默认只校验 Token 并回显成功,方便联调通路。

启动:
  cd backend
  python scripts/media_bridge_example.py
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("BRIDGE_PORT", "8765"))
# 与织火账号「Access Token」填同一个值
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "change-me-bridge-secret")


def publish_handler(payload: dict) -> dict:
    """把平台真实发布逻辑写在这里。payload 字段见下方注释。"""
    # payload 示例:
    # {
    #   "platform": "xhs",
    #   "account_name": "我的号",
    #   "title": "...",
    #   "content": "markdown...",
    #   "content_text": "纯文本...",
    #   "tags": ["a","b"],
    #   "word_count": 120
    # }
    print("[bridge] publish", json.dumps(payload, ensure_ascii=False)[:800])
    # TODO: 调用你自己的开放平台 / 合法自动化
    return {
        "ok": True,
        "id": "demo-" + str(abs(hash(payload.get("title") or "")) % 10_000_000),
        "url": None,
        "message": "示例桥接已收到;请在 publish_handler 接入真实发布",
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter
        print(f"[bridge] {self.address_string()} {fmt % args}")

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/", "/health"):
            self._json(200, {"ok": True, "service": "xiaohuo-media-bridge", "publish": "/publish"})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/publish":
            self._json(404, {"ok": False, "error": "use POST /publish"})
            return
        auth = self.headers.get("Authorization") or ""
        expected = f"Bearer {BRIDGE_TOKEN}"
        if auth.strip() != expected:
            self._json(401, {"ok": False, "error": "unauthorized: Bearer token mismatch"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "error": "body must be object"})
            return
        try:
            result = publish_handler(payload)
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)[:300]})
            return
        self._json(200, result)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Media bridge listening on http://{HOST}:{PORT}")
    print(f"  GET  /health")
    print(f"  POST /publish  Authorization: Bearer {BRIDGE_TOKEN}")
    print("Fill Zhihuo account → 发布 API 地址 = above URL, Access Token = BRIDGE_TOKEN")
    server.serve_forever()


if __name__ == "__main__":
    main()
