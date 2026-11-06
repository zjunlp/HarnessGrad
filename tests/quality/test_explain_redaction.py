"""Offline checks for explain transport errors and the existing retry contract."""
import http.client
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "console"))
import server  # noqa: E402

URL = "https://user-secret:password-secret@example.invalid/opaque-secret?token=query-secret"
KEY = "key-secret"
SECRETS = (URL, "user-secret", "password-secret", "opaque-secret", "query-secret", KEY, "body-secret")


@pytest.fixture(params=["round", "range"])
def call_api(request, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "find_run", lambda _: tmp_path)
    monkeypatch.setattr(server, "explain_config", lambda: (URL, "synthetic-model", KEY, []))
    monkeypatch.setattr(server, "read_curve", lambda _: ([{"round": 1}], None, None))
    monkeypatch.setattr(server, "build_explain_inputs", lambda *a: {"harness_sha": "sha"})
    monkeypatch.setattr(server, "build_explain_range_inputs", lambda *a: {})
    monkeypatch.setattr(server, "explain_prompt", lambda _: ("system", "user"))
    monkeypatch.setattr(server, "explain_range_prompt", lambda _: ("system", "user"))
    if request.param == "round":
        return lambda: server.api_explain("run", 1, force=True)
    return lambda: server.api_explain_range("run", 1, 2, force=True)


def assert_safe(result, expected):
    status, payload = result
    assert status == 502
    assert payload == {"error": "explain-upstream", "detail": expected}
    assert all(secret not in json.dumps(payload) for secret in SECRETS)


def test_http_error_body_never_exposed_and_no_format_fallback(call_api, monkeypatch):
    body = io.BytesIO((URL + KEY + "body-secret").encode())
    error = urllib.error.HTTPError(URL, 400, URL, {}, body)
    urlopen = Mock(side_effect=error)
    monkeypatch.setattr(server.urllib.request, "urlopen", urlopen)
    assert_safe(call_api(), "模型端点 HTTP 400")
    assert urlopen.call_count == 1
    assert json.loads(urlopen.call_args.args[0].data)["response_format"] == {"type": "json_object"}
    assert body.tell() == 0


def test_url_error_is_generic(call_api, monkeypatch):
    monkeypatch.setattr(server.urllib.request, "urlopen", Mock(side_effect=urllib.error.URLError(URL + KEY)))
    assert_safe(call_api(), "模型端点请求失败")


def test_invalid_url_is_generic(call_api, monkeypatch):
    monkeypatch.setattr(server.urllib.request, "urlopen", Mock(side_effect=http.client.InvalidURL(URL + KEY)))
    assert_safe(call_api(), "模型端点请求失败")


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_process_control_exceptions_propagate(monkeypatch, exception):
    monkeypatch.setattr(server.urllib.request, "urlopen", Mock(side_effect=exception))
    with pytest.raises(exception):
        server._chat_completion(URL, "model", KEY, "system", "user")


def test_request_value_error_is_generic(call_api, monkeypatch):
    monkeypatch.setattr(server.urllib.request, "Request", Mock(side_effect=ValueError(URL + KEY)))
    monkeypatch.setattr(server.urllib.request, "urlopen", Mock(side_effect=AssertionError("network called")))
    assert_safe(call_api(), "模型端点请求失败")


def test_model_json_error_does_not_echo_response(monkeypatch):
    response = io.BytesIO(("invalid-json " + URL + KEY).encode())
    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *a, **kw: response)
    with pytest.raises(server.ExplainUpstream, match="^模型端点返回的不是 JSON$"):
        server._chat_completion(URL, "model", KEY, "system", "user")


def test_invalid_model_reply_retries_with_json_format(call_api, monkeypatch):
    calls = []
    def urlopen(req, **kwargs):
        calls.append(json.loads(req.data))
        content = '{}' if len(calls) == 1 else json.dumps({"claim": "ok", "evidence": ["a", "b"], "risk": "r", "next": "n"})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(server.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(server, "_explain_cache_put", lambda *a: None)
    status, payload = call_api()
    assert status == 200 and payload["claim"] == "ok"
    assert len(calls) == 2
    assert all(call["response_format"] == {"type": "json_object"} for call in calls)
    assert "上一次的回复被拒绝" in calls[1]["messages"][0]["content"]
