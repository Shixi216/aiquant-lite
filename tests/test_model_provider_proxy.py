from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest
from pydantic import SecretStr

from router.config import router_settings
from router.providers.deepseek import DeepSeekProvider
from router.providers.longcat import LongCatProvider
from router.providers.qwen import QwenProvider


@contextmanager
def completion_server(label):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "body": body})
            payload = json.dumps({
                "model": "fixture-model", "choices": [{"finish_reason": "stop", "message": {
                    "content": label,
                }}], "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", [QwenProvider, DeepSeekProvider, LongCatProvider])
@pytest.mark.parametrize("bypass_proxy", [False, True])
async def test_model_clients_use_configured_proxy_and_honor_no_proxy(monkeypatch, provider_class, bypass_proxy):
    provider = provider_class()
    name = provider.provider_name
    with completion_server("via-proxy") as (proxy_url, proxy_requests), completion_server("direct-origin") as (origin_url, origin_requests):
        for variable in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
            monkeypatch.delenv(variable.lower(), raising=False)
            monkeypatch.setenv(variable, proxy_url)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1" if bypass_proxy else "")
        base = origin_url + "/v1" if bypass_proxy else "http://provider-unreachable.invalid/v1"
        monkeypatch.setattr(router_settings, name + "_api_key", SecretStr("fixture-key"))
        monkeypatch.setattr(router_settings, name + "_base_url", base)
        result = await provider.invoke(
            role="fixture", prompt="fixture", system_prompt="fixture", temperature=0, max_tokens=512,
        )
    assert result.content == ("direct-origin" if bypass_proxy else "via-proxy")
    assert len(proxy_requests) == (0 if bypass_proxy else 1)
    assert len(origin_requests) == (1 if bypass_proxy else 0)
    request = (origin_requests if bypass_proxy else proxy_requests)[0]
    assert request["body"]["max_tokens"] == 512
    if not bypass_proxy:
        assert request["path"] == "http://provider-unreachable.invalid/v1/chat/completions"
