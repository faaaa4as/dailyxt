from __future__ import annotations

import pytest

from content_hub_pack.clients.openrouter import generate_text
from content_hub_pack.models import AIConfig


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_generate_text_reports_openrouter_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        "content_hub_pack.clients.openrouter.requests.post",
        lambda *args, **kwargs: FakeResponse({"error": {"message": "Provider returned error", "code": 524}}),
    )

    with pytest.raises(RuntimeError, match="OpenRouter error 524: Provider returned error"):
        generate_text(
            AIConfig(provider="openrouter", model="test-model", base_url="https://openrouter.ai/api/v1"),
            "system",
            "user",
        )
