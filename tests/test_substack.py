from __future__ import annotations

from dataclasses import dataclass

import pytest

from content_hub_pack.clients.substack import _clean_html, fetch_new_posts
from content_hub_pack.models import PublicationConfig


@dataclass
class FakeResponse:
    text: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_fetch_new_posts_reads_feed_with_requests_response(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_xml = """
<rss version="2.0">
  <channel>
    <title>Your Brain on Money</title>
    <item>
      <title>If we're so sad, why are we still spending?</title>
      <link>https://yourbrainonmoney.substack.com/p/if-were-so-sad-why-are-we-still-spending</link>
      <author>author@example.com</author>
      <pubDate>Wed, 29 Apr 2026 12:09:47 GMT</pubDate>
      <description><![CDATA[<p>Post body</p>]]></description>
    </item>
  </channel>
</rss>
""".strip()
    calls: list[str] = []

    def fake_get(url: str, timeout: int) -> FakeResponse:
        calls.append(url)
        return FakeResponse(feed_xml)

    monkeypatch.setattr("content_hub_pack.clients.substack.requests.get", fake_get)

    posts = fetch_new_posts(
        [
            PublicationConfig(
                name="Your Brain on Money",
                base_url="https://yourbrainonmoney.substack.com",
                feed_url="https://yourbrainonmoney.substack.com/feed",
                priority=1,
                active=True,
            )
        ]
    )

    assert calls == ["https://yourbrainonmoney.substack.com/feed"]
    assert [post.post_url for post in posts] == [
        "https://yourbrainonmoney.substack.com/p/if-were-so-sad-why-are-we-still-spending"
    ]
    assert posts[0].title == "If we're so sad, why are we still spending?"
    assert posts[0].published_at == "Wed, 29 Apr 2026 12:09:47 GMT"


def test_fetch_new_posts_raises_when_feedparser_reports_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: int) -> FakeResponse:
        return FakeResponse("not xml")

    monkeypatch.setattr("content_hub_pack.clients.substack.requests.get", fake_get)

    with pytest.raises(RuntimeError, match="failed to parse feed"):
        fetch_new_posts(
            [
                PublicationConfig(
                    name="Broken",
                    base_url="https://broken.example",
                    feed_url="https://broken.example/feed",
                    priority=1,
                    active=True,
                )
            ]
        )


def test_clean_html_skips_decomposed_tags_when_removing_subscribe_blocks() -> None:
    cleaned = _clean_html('<div class="subscribe"><span class="child">remove me</span></div><p>keep me</p>')

    assert "remove me" not in cleaned
    assert "keep me" in cleaned
