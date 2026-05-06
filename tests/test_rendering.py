from __future__ import annotations

import zipfile
from pathlib import Path

from content_hub_pack.rendering import render_substack_book


class FakeImageResponse:
    headers = {"content-type": "image/png"}
    content = b"\x89PNG\r\n\x1a\nimage-bytes"

    def raise_for_status(self) -> None:
        return None


def test_render_substack_book_embeds_remote_images_and_rewrites_src(tmp_path: Path, monkeypatch) -> None:
    requested: list[str] = []

    def fake_get(url: str, timeout: int) -> FakeImageResponse:
        requested.append(url)
        return FakeImageResponse()

    monkeypatch.setattr("content_hub_pack.rendering.requests.get", fake_get)
    output_path = tmp_path / "substack.epub"

    render_substack_book(
        "Substack",
        output_path,
        [
            {
                "title": "Post",
                "publication_name": "Pub",
                "author": "Author",
                "published_at": "Mon, 04 May 2026 12:20:53 GMT",
                "post_url": "https://example.com/p/post",
                "body_html": '<p>Before</p><img src="https://cdn.example.com/image.png?width=1200" alt="chart"><p>After</p>',
            }
        ],
    )

    with zipfile.ZipFile(output_path) as epub_file:
        names = epub_file.namelist()
        image_names = [name for name in names if name.startswith("EPUB/images/") and name.endswith(".png")]
        chapter_name = next(name for name in names if name.endswith("post.xhtml"))
        chapter_html = epub_file.read(chapter_name).decode("utf-8")

    assert requested == ["https://cdn.example.com/image.png?width=1200"]
    assert len(image_names) == 1
    assert 'src="images/' in chapter_html
    assert "https://cdn.example.com/image.png" not in chapter_html
    assert "Before" in chapter_html
    assert "After" in chapter_html
