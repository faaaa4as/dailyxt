from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from content_hub_pack.models import HackerNewsItem


BASE_URL = "https://hacker-news.firebaseio.com/v0"
ARTICLE_MAX_CHARS = 30_000
ARTICLE_MIN_CHARS = 200
USER_AGENT = "content-hub/0.1 (+https://github.com/example/content-hub)"


def _get(path: str):
    response = requests.get(f"{BASE_URL}/{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_top_story_ids(limit: int) -> list[int]:
    return _get("topstories.json")[:limit]


def fetch_item(item_id: int) -> dict:
    return _get(f"item/{item_id}.json")


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _article_container(soup: BeautifulSoup):
    return soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup


def fetch_article_text(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "", "unsupported_url"
    if parsed.netloc == "news.ycombinator.com":
        return "", "hn_self"
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except Exception:
        return "", "fetch_error"

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "application/xhtml" not in content_type and "text/plain" not in content_type:
        return "", "non_html"
    if "text/plain" in content_type:
        text = _normalize_whitespace(response.text)
    else:
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.select("script, style, nav, footer, header, aside, form, noscript, svg"):
            tag.decompose()
        text = _normalize_whitespace(_article_container(soup).get_text(" "))
    if not text:
        return "", "empty"
    if len(text) < ARTICLE_MIN_CHARS:
        return text[:ARTICLE_MAX_CHARS], "too_short"
    return text[:ARTICLE_MAX_CHARS], "ok"


def fetch_candidate_items(limit: int) -> list[HackerNewsItem]:
    items: list[HackerNewsItem] = []
    for item_id in fetch_top_story_ids(limit):
        raw = fetch_item(item_id)
        if not raw or raw.get("type") != "story" or raw.get("title", "").lower().startswith("ask hn: who is hiring"):
            continue
        comments: list[str] = []
        for comment_id in raw.get("kids", [])[:5]:
            comment_raw = fetch_item(comment_id)
            if comment_raw and comment_raw.get("text"):
                comments.append(comment_raw["text"])
        created_at = datetime.fromtimestamp(raw.get("time", 0), tz=timezone.utc).isoformat()
        article_text, article_fetch_status = fetch_article_text(raw.get("url", f"https://news.ycombinator.com/item?id={raw['id']}"))
        items.append(
            HackerNewsItem(
                hn_id=int(raw["id"]),
                title=raw.get("title", "Untitled"),
                url=raw.get("url", f"https://news.ycombinator.com/item?id={raw['id']}"),
                author=raw.get("by", "unknown"),
                score=int(raw.get("score") or 0),
                created_at=created_at,
                text=raw.get("text", ""),
                top_comments=comments,
                article_text=article_text,
                article_fetch_status=article_fetch_status,
            )
        )
    return items
