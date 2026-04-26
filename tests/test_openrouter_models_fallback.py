"""OpenRouter advisor: free-model cascade and parsing helpers."""

from unittest.mock import MagicMock, patch

import requests

from notifications import openrouter_advisor as ora


def test_assistant_text_from_completion_empty():
    assert ora._assistant_text_from_completion({}) == ""
    assert ora._assistant_text_from_completion({"choices": []}) == ""
    assert ora._assistant_text_from_completion(None) == ""


def test_assistant_text_from_completion_ok():
    data = {"choices": [{"message": {"content": "  hello  "}}]}
    assert ora._assistant_text_from_completion(data) == "hello"


def test_chat_try_models_first_ok_stops():
    calls: list[str] = []

    def fake_once(payload: dict) -> MagicMock:
        calls.append(payload["model"])
        r = MagicMock()
        r.json.return_value = {"choices": [{"message": {"content": "pong"}}]}
        return r

    with patch.object(ora, "_openrouter_chat_once", side_effect=fake_once):
        txt, used = ora._chat_completions_try_models(
            [{"role": "user", "content": "x"}], 0.1, 32, ["m-a", "m-b"]
        )
    assert txt == "pong"
    assert used == "m-a"
    assert calls == ["m-a"]


def test_chat_try_models_skips_429_then_ok():
    calls: list[str] = []

    def fake_once(payload: dict) -> MagicMock:
        calls.append(payload["model"])
        if payload["model"] == "m1":
            resp = MagicMock()
            resp.status_code = 429
            raise requests.HTTPError(response=resp)
        r = MagicMock()
        r.json.return_value = {"choices": [{"message": {"content": "from-m2"}}]}
        return r

    with patch.object(ora, "_openrouter_chat_once", side_effect=fake_once):
        txt, used = ora._chat_completions_try_models(
            [{"role": "user", "content": "x"}], 0.2, 64, ["m1", "m2"]
        )
    assert used == "m2"
    assert txt == "from-m2"
    assert calls == ["m1", "m2"]


def test_chat_try_models_skips_empty_content():
    def fake_once(payload: dict) -> MagicMock:
        r = MagicMock()
        if payload["model"] == "bad":
            r.json.return_value = {"choices": [{"message": {"content": ""}}]}
        else:
            r.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return r

    with patch.object(ora, "_openrouter_chat_once", side_effect=fake_once):
        txt, used = ora._chat_completions_try_models(
            [{"role": "user", "content": "x"}], 0.1, 32, ["bad", "good"]
        )
    assert used == "good"
    assert txt == "ok"


def test_chat_try_models_all_fail_message():
    def fake_once(_payload: dict) -> MagicMock:
        raise requests.HTTPError(response=MagicMock(status_code=503))

    with patch.object(ora, "_openrouter_chat_once", side_effect=fake_once):
        txt, used = ora._chat_completions_try_models(
            [{"role": "user", "content": "x"}], 0.1, 32, ["a", "b"]
        )
    assert used is None
    assert "all configured models failed" in txt
    assert "HTTP 503" in txt


def test_openrouter_model_candidates_env_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "only/one:free")
    assert ora.openrouter_model_candidates() == ["only/one:free"]


def test_openrouter_model_candidates_default_order(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    got = ora.openrouter_model_candidates()
    assert got[0] == "openrouter/free"
    assert "meta-llama/llama-3.2-3b-instruct:free" in got
    assert len(got) == len(ora.OPENROUTER_FREE_MODELS)
