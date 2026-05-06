from __future__ import annotations

import pytest

from content_hub_pack.clients.hn import fetch_candidate_items


class FakeResponse:
    def __init__(self, *, json_payload=None, text: str = "", content_type: str = "application/json", status_code: int = 200):
        self._json_payload = json_payload
        self.text = text
        self.headers = {"content-type": content_type}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_payload


def test_fetch_candidate_items_extracts_linked_article_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: int, **kwargs) -> FakeResponse:
        if url.endswith("topstories.json"):
            return FakeResponse(json_payload=[123])
        if url.endswith("item/123.json"):
            return FakeResponse(
                json_payload={
                    "id": 123,
                    "type": "story",
                    "title": "Redis arrays",
                    "url": "https://example.com/post",
                    "by": "antirez",
                    "score": 50,
                    "time": 1_700_000_000,
                    "kids": [456],
                }
            )
        if url.endswith("item/456.json"):
            return FakeResponse(json_payload={"id": 456, "type": "comment", "text": "HN reaction"})
        if url == "https://example.com/post":
            return FakeResponse(
                text="""
<html><body><nav>nav junk</nav><article><h1>Redis arrays</h1><p>Article source truth about database memory layout and cache behavior. This article explains LLM inference, developer infrastructure, agents, SQL storage, and cache performance with enough detail to be treated as a real fetched source rather than a tiny stub.</p></article><script>bad()</script></body></html>
""".strip(),
                content_type="text/html; charset=utf-8",
            )
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("content_hub_pack.clients.hn.requests.get", fake_get)

    item = fetch_candidate_items(1)[0]

    assert item.article_fetch_status == "ok"
    assert "database memory layout" in item.article_text
    assert "nav junk" not in item.article_text
    assert item.top_comments == ["HN reaction"]


def test_fetch_candidate_items_keeps_story_when_article_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: int, **kwargs) -> FakeResponse:
        if url.endswith("topstories.json"):
            return FakeResponse(json_payload=[123])
        if url.endswith("item/123.json"):
            return FakeResponse(
                json_payload={
                    "id": 123,
                    "type": "story",
                    "title": "PDF paper",
                    "url": "https://example.com/paper.pdf",
                    "by": "alice",
                    "score": 50,
                    "time": 1_700_000_000,
                }
            )
        if url == "https://example.com/paper.pdf":
            return FakeResponse(text="%PDF", content_type="application/pdf")
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("content_hub_pack.clients.hn.requests.get", fake_get)

    item = fetch_candidate_items(1)[0]

    assert item.article_fetch_status == "non_html"
    assert item.article_text == ""
    assert item.title == "PDF paper"
