"""Minimal OpenAI-compatible mock of llama-server (stdlib only) for tests and the `llm-mock` fallback.

- GET /health -> {"status": "ok"}
- POST /v1/chat/completions: if response_format carries a JSON schema, returns a synthesized
  instance of it; otherwise echoes a fixed text. Model name "mock-invalid" always returns invalid JSON.
Run: python -m b2c_llm_gateway.mock_server --port 8080
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def synthesize(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:
        return synthesize(defs[schema["$ref"].split("/")[-1]], defs)
    for k in ("anyOf", "oneOf", "allOf"):
        if k in schema:
            return synthesize(schema[k][0], defs)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    t = schema.get("type")
    if t == "object":
        return {k: synthesize(v, defs) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [synthesize(schema.get("items", {"type": "string"}), defs)]
    if t == "integer":
        return max(0, int(schema.get("minimum", 0)))
    if t == "number":
        return float(schema.get("minimum", 0))
    if t == "boolean":
        return True
    if t == "null":
        return None
    return "mock"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/v1/health"):
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": "not found"})
            return
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        model = req.get("model", "mock")
        rf = req.get("response_format") or {}
        schema = (rf.get("json_schema") or {}).get("schema") or rf.get("schema")
        if model == "mock-invalid":
            content = "this is not json"
        elif schema:
            content = json.dumps(synthesize(schema))
        else:
            content = "mock response"
        self._send(200, {
            "id": "mock", "object": "chat.completion", "model": f"{model}.mock",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": sum(len(str(m.get("content", ""))) // 4 for m in req["messages"]),
                      "completion_tokens": max(1, len(content) // 4)},
        })

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


def make_server(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    make_server(a.host, a.port).serve_forever()


if __name__ == "__main__":
    main()
