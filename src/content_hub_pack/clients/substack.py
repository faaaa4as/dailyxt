from __future__ import annotations

import hashlib
from typing import Iterable

import feedparser
import requests
from bs4 import BeautifulSoup

from content_hub_pack.models import PublicationConfig, SubstackPost


def _clean_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup.select("script, style, iframe, noscript, form, button, nav, aside"):
        tag.decompose()
    for element in soup.find_all(attrs={"class": True}):
        if element.attrs is None:
            continue
        classes = " ".join(element.get("class", []))
        if any(token in classes.lower() for token in ["subscribe", "share", "signup", "footer", "header"]):
            element.decompose()
    return str(soup)


def _parse_feed(publication: PublicationConfig) -> feedparser.FeedParserDict:
    response = requests.get(publication.feed_url, timeout=30)
    response.raise_for_status()
    feed = feedparser.parse(response.text)
    if feed.bozo:
        raise RuntimeError(f"failed to parse feed {publication.feed_url}: {feed.bozo_exception}")
    return feed


def _entry_body(entry: feedparser.FeedParserDict, fallback_url: str) -> str:
    if entry.get("content"):
        return _clean_html(entry["content"][0].value)
    if entry.get("summary"):
        return _clean_html(entry["summary"])
    response = requests.get(fallback_url, timeout=30)
    response.raise_for_status()
    return _clean_html(response.text)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_new_posts(publications: Iterable[PublicationConfig]) -> list[SubstackPost]:
    posts: list[SubstackPost] = []
    for publication in sorted(publications, key=lambda item: item.priority):
        if not publication.active:
            continue
        feed = _parse_feed(publication)
        for entry in feed.entries:
            url = entry.get("link", "")
            body_html = _entry_body(entry, url)
            posts.append(
                SubstackPost(
                    publication_name=publication.name,
                    post_url=url,
                    title=entry.get("title", "Untitled"),
                    author=entry.get("author", publication.name),
                    published_at=entry.get("published", entry.get("updated", "")),
                    body_html=body_html,
                )
            )
    return posts
