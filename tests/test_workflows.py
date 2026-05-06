from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from content_hub_pack.db import ensure_database, save_substack_posts
from content_hub_pack.models import (
    AIConfig,
    DeliveryConfig,
    HackerNewsConfig,
    HackerNewsItem,
    OutputConfig,
    PlaylistConfig,
    PublicationConfig,
    RuntimeConfig,
    Settings,
    SubstackPost,
    YoutubeConfig,
    YoutubeVideo,
)
from content_hub_pack.workflows import _hn_entry, _video_entry, compose_daily_pack_stage, ingest_substack_stage, ingest_youtube_stage


def test_compose_daily_pack_regenerates_stale_hn_brief_entries(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(
        project_root=tmp_path,
        timezone="Europe/Paris",
        schedule="30 6 * * *",
        substack_publications=[],
        youtube=YoutubeConfig(playlists=[]),
        hackernews=HackerNewsConfig(final_count=2, min_relevance_score=14.0, min_selected_items=2),
        delivery=DeliveryConfig(method="local_only", target_path=tmp_path),
        output=OutputConfig(root_dir=tmp_path, folder_prefix="output", folder_date_format="%d-%m-%Y"),
        runtime=RuntimeConfig(db_path=tmp_path / "data/runtime/test.sqlite3", staging_dir=tmp_path / "data/staging"),
        ai=AIConfig(provider="openrouter", model="nvidia/nemotron-3-super-120b-a12b:free", base_url="https://openrouter.ai/api/v1"),
    )

    staging_dir = tmp_path / "data/staging/2026-04-28"
    staging_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir = tmp_path / "output_28-04-2026"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    hn_items = [
        {
            "hn_id": 101,
            "title": "Men who stare at walls",
            "url": "https://example.com/walls",
            "author": "alice",
            "score": 200,
            "created_at": "2026-04-28T06:00:00+00:00",
            "text": "",
            "top_comments": [],
            "relevance_score": 25.0,
        },
        {
            "hn_id": 102,
            "title": "Talkie: a 13B vintage language model from 1930",
            "url": "https://example.com/talkie",
            "author": "bob",
            "score": 180,
            "created_at": "2026-04-28T06:00:00+00:00",
            "text": "",
            "top_comments": [],
            "relevance_score": 24.0,
        },
    ]
    (staging_dir / "hn_items.json").write_text(json.dumps(hn_items, indent=2))
    (staging_dir / "hn_brief_entries.json").write_text(
        json.dumps(
            [
                {
                    "hn_id": 1,
                    "headline": "Old technical item",
                    "title": "Old technical item",
                    "url": "https://example.com/old",
                    "body": "Old cached body",
                }
            ],
            indent=2,
        )
    )

    monkeypatch.setattr(
        "content_hub_pack.workflows._hn_entry",
        lambda settings, item: {
            "hn_id": item.hn_id,
            "headline": item.title,
            "title": item.title,
            "url": item.url,
            "body": f"Fresh body for {item.title}",
        },
    )

    def fake_render_text_book(title: str, output_path: Path, entries: list[dict[str, str]], source_key: str) -> Path:
        output_path.write_text(json.dumps(entries, indent=2))
        return output_path

    monkeypatch.setattr("content_hub_pack.workflows.render_text_book", fake_render_text_book)

    result = compose_daily_pack_stage(settings, "2026-04-28")

    refreshed_entries = json.loads((staging_dir / "hn_brief_entries.json").read_text())
    assert [entry["hn_id"] for entry in refreshed_entries] == [101, 102]
    assert any(path.endswith("03-hn-brief.epub") for path in result.details["artifacts"])


def _substack_settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        timezone="Europe/Paris",
        schedule="30 6 * * *",
        substack_publications=[
            PublicationConfig(
                name="Example",
                base_url="https://example.substack.com",
                feed_url="https://example.substack.com/feed",
                priority=1,
            )
        ],
        youtube=YoutubeConfig(playlists=[]),
        hackernews=HackerNewsConfig(),
        delivery=DeliveryConfig(method="local_only", target_path=tmp_path),
        output=OutputConfig(root_dir=tmp_path, folder_prefix="output", folder_date_format="%d-%m-%Y"),
        runtime=RuntimeConfig(db_path=tmp_path / "data/runtime/test.sqlite3", staging_dir=tmp_path / "data/staging"),
        ai=AIConfig(provider="openrouter", model="nvidia/nemotron-3-super-120b-a12b:free", base_url="https://openrouter.ai/api/v1"),
    )


def _post(title: str, url: str, published_at: str, publication_name: str = "Example") -> SubstackPost:
    return SubstackPost(
        publication_name=publication_name,
        post_url=url,
        title=title,
        author=publication_name,
        published_at=published_at,
        body_html=f"<p>{title}</p>",
    )


def test_ingest_substack_first_run_stages_only_latest_post_per_publication(tmp_path: Path, monkeypatch) -> None:
    settings = _substack_settings(tmp_path)
    settings.runtime.db_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_database(settings.runtime.db_path)
    old_post = _post("Old", "https://example.substack.com/p/old", "Sat, 27 Sep 2025 23:31:36 GMT")
    latest_post = _post("Latest", "https://example.substack.com/p/latest", "Mon, 04 May 2026 12:20:53 GMT")
    monkeypatch.setattr("content_hub_pack.workflows.fetch_new_posts", lambda publications: [old_post, latest_post])

    result = ingest_substack_stage(settings, "2026-05-04")

    payload = json.loads((tmp_path / "data/staging/2026-05-04/substack_posts.json").read_text())
    assert result.details["new_posts"] == 1
    assert [post["post_url"] for post in payload] == ["https://example.substack.com/p/latest"]


def test_ingest_substack_stages_only_latest_unseen_post_per_publication(tmp_path: Path, monkeypatch) -> None:
    settings = _substack_settings(tmp_path)
    settings.runtime.db_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_database(settings.runtime.db_path)
    known_post = _post("Known", "https://example.substack.com/p/known", "Mon, 27 Apr 2026 12:30:50 GMT")
    save_substack_posts(settings.runtime.db_path, [known_post], run_id=1, content_hashes={known_post.post_url: "known-hash"})
    new_old_post = _post("New Old", "https://example.substack.com/p/new-old", "Wed, 29 Apr 2026 12:09:47 GMT")
    new_latest_post = _post("New Latest", "https://example.substack.com/p/new-latest", "Mon, 04 May 2026 12:20:53 GMT")
    monkeypatch.setattr(
        "content_hub_pack.workflows.fetch_new_posts",
        lambda publications: [known_post, new_old_post, new_latest_post],
    )

    result = ingest_substack_stage(settings, "2026-05-04")

    payload = json.loads((tmp_path / "data/staging/2026-05-04/substack_posts.json").read_text())
    assert result.details["new_posts"] == 1
    assert [post["post_url"] for post in payload] == ["https://example.substack.com/p/new-latest"]


def test_hn_entry_prompt_uses_article_text_as_source_of_truth(tmp_path: Path, monkeypatch) -> None:
    settings = _video_settings(tmp_path)
    captured: dict[str, str] = {}

    def fake_generate_text(ai, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        captured["prompt"] = user_prompt
        return "brief"

    monkeypatch.setattr("content_hub_pack.workflows.generate_text", fake_generate_text)
    item = HackerNewsItem(
        hn_id=900,
        title="Interesting source article",
        url="https://example.com/source",
        author="alice",
        score=123,
        created_at="2026-05-04T06:00:00+00:00",
        text="HN story text",
        top_comments=["comment should be secondary"],
        article_text="article source truth about LLM agents and inference",
        article_fetch_status="ok",
        relevance_score=22.0,
    )

    entry = _hn_entry(settings, item)

    assert entry["body"] == "brief"
    assert entry["source_version"] == "article-v1"
    assert "Article text (primary source of truth):" in captured["prompt"]
    assert "article source truth" in captured["prompt"]
    assert "HN comments (secondary discussion context, not source of truth):" in captured["prompt"]
    assert captured["prompt"].index("Article text") < captured["prompt"].index("HN comments")


def _video_settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        timezone="Europe/Paris",
        schedule="30 6 * * *",
        substack_publications=[],
        youtube=YoutubeConfig(playlists=[]),
        hackernews=HackerNewsConfig(),
        delivery=DeliveryConfig(method="local_only", target_path=tmp_path),
        output=OutputConfig(root_dir=tmp_path, folder_prefix="output", folder_date_format="%d-%m-%Y"),
        runtime=RuntimeConfig(db_path=tmp_path / "data/runtime/test.sqlite3", staging_dir=tmp_path / "data/staging"),
        ai=AIConfig(provider="openrouter", model="nvidia/nemotron-3-super-120b-a12b:free", base_url="https://openrouter.ai/api/v1"),
    )


def _video(video_id: str, transcript: str = "short transcript") -> YoutubeVideo:
    return YoutubeVideo(
        video_id=video_id,
        title=f"Video {video_id}",
        channel="Example Channel",
        published_at="2026-05-04T12:00:00Z",
        duration_seconds=300,
        url=f"https://youtu.be/{video_id}",
        transcript_status="available",
        transcript_text=transcript,
        description="Example description",
    )


def test_ingest_youtube_fetches_past_processed_playlist_prefix(tmp_path: Path, monkeypatch) -> None:
    settings = _video_settings(tmp_path)
    settings.youtube = YoutubeConfig(
        playlists=[PlaylistConfig(name="Primary", playlist_id="playlist-1", active=True)],
        max_videos_per_day=2,
    )
    settings.runtime.db_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_database(settings.runtime.db_path)
    monkeypatch.setattr("content_hub_pack.workflows.existing_youtube_article_ids", lambda db_path: {"v1", "v2"})

    requested_limits: list[int] = []

    def fake_fetch_playlist_videos(playlist_id: str, max_items: int) -> list[YoutubeVideo]:
        requested_limits.append(max_items)
        videos = [_video(f"v{idx}") for idx in range(1, 7)]
        return videos[:max_items]

    monkeypatch.setattr("content_hub_pack.workflows.fetch_playlist_videos", fake_fetch_playlist_videos)

    result = ingest_youtube_stage(settings, "2026-05-04")

    payload = json.loads((tmp_path / "data/staging/2026-05-04/youtube_videos.json").read_text())
    assert requested_limits == [10]
    assert result.details["new_videos"] == 2
    assert [video["video_id"] for video in payload] == ["v3", "v4"]


def test_video_entry_splits_long_transcript_into_multiple_small_openrouter_calls(tmp_path: Path, monkeypatch) -> None:
    settings = _video_settings(tmp_path)
    prompts: list[str] = []

    def fake_generate_text(ai, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        prompts.append(user_prompt)
        return f"summary {len(prompts)}"

    monkeypatch.setattr("content_hub_pack.workflows.generate_text", fake_generate_text)

    entry = _video_entry(settings, _video("long", "word " * 12000))

    assert entry["body"] == "summary 5"
    assert len(prompts) == 5
    assert all(len(prompt) < 20000 for prompt in prompts)


def test_compose_daily_pack_persists_video_notes_after_each_video(tmp_path: Path, monkeypatch) -> None:
    settings = _video_settings(tmp_path)
    staging_dir = tmp_path / "data/staging/2026-05-04"
    staging_dir.mkdir(parents=True)
    (staging_dir / "substack_posts.json").write_text("[]")
    (staging_dir / "hn_items.json").write_text("[]")
    (staging_dir / "youtube_videos.json").write_text(
        json.dumps([asdict(_video("first")), asdict(_video("second"))], indent=2)
    )

    def fake_video_entry(settings: Settings, video: YoutubeVideo) -> dict[str, str]:
        if video.video_id == "second":
            raise RuntimeError("boom")
        return {"video_id": video.video_id, "title": video.title, "article_title": video.title, "url": video.url, "body": "ok"}

    monkeypatch.setattr("content_hub_pack.workflows._video_entry", fake_video_entry)

    try:
        compose_daily_pack_stage(settings, "2026-05-04")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("compose_daily_pack_stage should fail on second video")

    notes = json.loads((staging_dir / "video_notes.json").read_text())
    assert [note["video_id"] for note in notes] == ["first"]


def test_compose_daily_pack_generates_only_missing_video_notes(tmp_path: Path, monkeypatch) -> None:
    settings = _video_settings(tmp_path)
    settings.runtime.db_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_database(settings.runtime.db_path)
    staging_dir = tmp_path / "data/staging/2026-05-04"
    staging_dir.mkdir(parents=True)
    (staging_dir / "substack_posts.json").write_text("[]")
    (staging_dir / "hn_items.json").write_text("[]")
    (staging_dir / "youtube_videos.json").write_text(
        json.dumps([asdict(_video("first")), asdict(_video("second"))], indent=2)
    )
    (staging_dir / "video_notes.json").write_text(
        json.dumps([{"video_id": "first", "title": "Video first", "article_title": "Video first", "url": "https://youtu.be/first", "body": "cached"}])
    )

    generated: list[str] = []

    def fake_video_entry(settings: Settings, video: YoutubeVideo) -> dict[str, str]:
        generated.append(video.video_id)
        return {"video_id": video.video_id, "title": video.title, "article_title": video.title, "url": video.url, "body": "new"}

    def fake_render_text_book(title: str, output_path: Path, entries: list[dict[str, str]], source_key: str) -> Path:
        output_path.write_text(json.dumps(entries))
        return output_path

    monkeypatch.setattr("content_hub_pack.workflows._video_entry", fake_video_entry)
    monkeypatch.setattr("content_hub_pack.workflows.render_text_book", fake_render_text_book)

    compose_daily_pack_stage(settings, "2026-05-04")

    notes = json.loads((staging_dir / "video_notes.json").read_text())
    assert generated == ["second"]
    assert [note["video_id"] for note in notes] == ["first", "second"]
