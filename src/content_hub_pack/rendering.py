from __future__ import annotations

import hashlib
import html
import mimetypes
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from ebooklib import epub


def _paragraphize(text: str) -> str:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    return "".join(f"<p>{html.escape(part)}</p>" for part in paragraphs)


def write_epub(title: str, output_path: Path, chapters: Iterable[tuple[str, str, str]], extra_items: Iterable[epub.EpubItem] = ()) -> Path:
    book = epub.EpubBook()
    book.set_identifier(output_path.stem)
    book.set_title(title)
    book.set_language("en")

    toc = []
    spine = ["nav"]
    items = []

    for extra_item in extra_items:
        book.add_item(extra_item)

    for index, (chapter_title, chapter_file, chapter_html) in enumerate(chapters, start=1):
        item = epub.EpubHtml(
            title=chapter_title,
            file_name=f"{index:02d}-{chapter_file}.xhtml",
            lang="en",
        )
        item.content = chapter_html
        book.add_item(item)
        toc.append(item)
        spine.append(item)
        items.append(item)

    book.toc = tuple(toc)
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(output_path), book)
    return output_path


def _image_extension(url: str, content_type: str) -> str:
    guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) if content_type else None
    if guessed:
        return ".jpg" if guessed == ".jpe" else guessed
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"} else ".jpg"


def _image_media_type(content_type: str, file_name: str) -> str:
    media_type = content_type.split(";", 1)[0].strip() if content_type else ""
    if media_type.startswith("image/"):
        return media_type
    return mimetypes.guess_type(file_name)[0] or "image/jpeg"


def _embed_remote_images(body_html: str, image_items: dict[str, epub.EpubItem]) -> str:
    soup = BeautifulSoup(body_html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src or not src.startswith(("http://", "https://")):
            continue
        if src not in image_items:
            response = requests.get(src, timeout=30)
            response.raise_for_status()
            extension = _image_extension(src, response.headers.get("content-type", ""))
            digest = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
            file_name = f"images/{digest}{extension}"
            image_items[src] = epub.EpubItem(
                uid=f"image-{digest}",
                file_name=file_name,
                media_type=_image_media_type(response.headers.get("content-type", ""), file_name),
                content=response.content,
            )
        img["src"] = image_items[src].file_name
        for attr in ("srcset", "data-src", "data-srcset"):
            if img.has_attr(attr):
                del img[attr]
    return str(soup)


def render_substack_book(title: str, output_path: Path, entries: list[dict[str, str]]) -> Path:
    chapters = []
    image_items: dict[str, epub.EpubItem] = {}
    for entry in entries:
        body_html = _embed_remote_images(entry["body_html"], image_items)
        chapter_html = (
            f"<h1>{html.escape(entry['title'])}</h1>"
            f"<p><strong>{html.escape(entry['publication_name'])}</strong> | "
            f"{html.escape(entry['author'])} | {html.escape(entry['published_at'])}</p>"
            f"<p>Source: <a href=\"{html.escape(entry['post_url'])}\">{html.escape(entry['post_url'])}</a></p>"
            f"{body_html}"
        )
        chapters.append((entry["title"], entry["title"].lower().replace(" ", "-"), chapter_html))
    return write_epub(title, output_path, chapters, image_items.values())


def render_text_book(title: str, output_path: Path, entries: list[dict[str, str]], source_key: str) -> Path:
    chapters = []
    for entry in entries:
        header = f"<h1>{html.escape(entry['title'])}</h1>"
        source_url = entry[source_key]
        meta = f"<p>Source: <a href=\"{html.escape(source_url)}\">{html.escape(source_url)}</a></p>"
        body = _paragraphize(entry["body"])
        chapters.append((entry["title"], entry["title"].lower().replace(" ", "-"), header + meta + body))
    return write_epub(title, output_path, chapters)
