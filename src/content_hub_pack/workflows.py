from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from content_hub_pack.clients.hn import fetch_candidate_items
from content_hub_pack.clients.openrouter import generate_text
from content_hub_pack.clients.substack import content_hash, fetch_new_posts
from content_hub_pack.clients.youtube import fetch_playlist_videos
from content_hub_pack.config import ensure_runtime_directories, load_settings, run_artifact_dir, run_staging_dir
from content_hub_pack.db import (
    ensure_database,
    existing_substack_urls,
    existing_youtube_article_ids,
    finish_run,
    latest_run_status,
    record_artifact,
    save_hn_items,
    save_substack_posts,
    save_youtube_articles,
    save_youtube_items,
    seed_publications,
    start_run,
)
from content_hub_pack.models import HackerNewsItem, Settings, StageResult, SubstackPost, YoutubeVideo
from content_hub_pack.ranking import load_hn_personality, rank_items
from content_hub_pack.rendering import render_substack_book, render_text_book

LOGGER = logging.getLogger(__name__)

VIDEO_TRANSCRIPT_CHUNK_CHARS = 15_000
YOUTUBE_PLAYLIST_CANDIDATE_MULTIPLIER = 5

YOUTUBE_SYSTEM_PROMPT = """
Convert the supplied YouTube transcript into a compelling article suitable for e-ink reading.
Be faithful to the speaker's ideas.
Remove repetition, ad reads, rambling transitions, and speech disfluencies.
Preserve technical nuance.
Use short sections and readable prose.
Do not invent claims not supported by the transcript.
Do not over-dramatize.
Use this structure:
- Title
- Why it matters
- Core thesis
- Main points
- Notable examples or frameworks
- Practical takeaways
""".strip()

HN_SYSTEM_PROMPT = """
You are writing a daily Hacker News brief for a curious technical reader interested in AI agents, LLM tooling, developer infrastructure, crypto market structure, exchanges, trading systems, quant ideas, programming essays, security, research workflows, computing history, vintage tech, architecture and memory stories, and thoughtful essays about cognition, attention, and how people think.
Only summarize stories with a genuine match to those interests.
Do not force relevance where there is none.
Prefer signal over popularity.
Avoid generic summaries and shallow hype.
Do not make every story sound like infra or finance if it is really about culture, history, cognition, or curiosity.
Do not mention the reader's name.
Do not write personalized headings like "Why it matters to the reader".
Do not force analogies to DeFi, AI, or markets when they are not actually present in the story.
Use direct prose, not bullets.
Keep each entry under 140 words.
Structure:
1. One short opening sentence on the real reason the item is worth attention.
2. Two to four sentences of concrete summary.
3. Optional final sentence with a plain read priority, only if warranted.
""".strip()


def _now(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


def _read_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _normalize_text_entry(entry: dict, title_keys: tuple[str, ...], body_keys: tuple[str, ...]) -> dict[str, str] | None:
    title = next((entry.get(key) for key in title_keys if entry.get(key)), None)
    body = next((entry.get(key) for key in body_keys if entry.get(key)), None)
    url = entry.get("url", "")
    if not title or not body:
        return None
    return {"title": title, "body": body, "url": url}


def _entries_match_expected_ids(entries: list[dict], id_key: str, expected_ids: list[int | str]) -> bool:
    entry_ids = [entry.get(id_key) for entry in entries if entry.get(id_key) is not None]
    return entry_ids == expected_ids


def _hn_entries_current(entries: list[dict], expected_ids: list[int]) -> bool:
    return _entries_match_expected_ids(entries, "hn_id", expected_ids) and all(
        entry.get("source_version") == "article-v1" for entry in entries
    )


def _chunk_text(text: str, chunk_chars: int = VIDEO_TRANSCRIPT_CHUNK_CHARS) -> list[str]:
    return [text[index : index + chunk_chars] for index in range(0, len(text), chunk_chars)] or [""]


def _video_prompt(video: YoutubeVideo, transcript_text: str) -> str:
    return (
        f"Video title: {video.title}\n"
        f"Channel: {video.channel}\n"
        f"Published: {video.published_at}\n"
        f"URL: {video.url}\n"
        f"Description: {video.description}\n\n"
        f"Transcript:\n{transcript_text}"
    )


def _video_entry(settings: Settings, video: YoutubeVideo) -> dict[str, str]:
    transcript_chunks = _chunk_text(video.transcript_text)
    if len(transcript_chunks) == 1:
        body = generate_text(settings.ai, YOUTUBE_SYSTEM_PROMPT, _video_prompt(video, transcript_chunks[0]))
    else:
        chunk_summaries = []
        for index, chunk in enumerate(transcript_chunks, start=1):
            chunk_prompt = (
                f"Video title: {video.title}\n"
                f"URL: {video.url}\n"
                f"Transcript chunk {index} of {len(transcript_chunks)}:\n{chunk}\n\n"
                "Summarize this chunk only. Preserve concrete claims, examples, and key details."
            )
            chunk_summaries.append(generate_text(settings.ai, YOUTUBE_SYSTEM_PROMPT, chunk_prompt))
        body = generate_text(
            settings.ai,
            YOUTUBE_SYSTEM_PROMPT,
            (
                f"Video title: {video.title}\n"
                f"Channel: {video.channel}\n"
                f"Published: {video.published_at}\n"
                f"URL: {video.url}\n"
                f"Description: {video.description}\n\n"
                "Chunk summaries:\n- " + "\n- ".join(chunk_summaries) + "\n\n"
                "Write the final coherent video note from these chunk summaries."
            ),
        )
    return {
        "video_id": video.video_id,
        "title": video.title,
        "article_title": video.title,
        "url": video.url,
        "body": body,
    }


def _hn_entry(settings: Settings, item: HackerNewsItem) -> dict[str, str]:
    article_text = item.article_text or "[linked article text unavailable]"
    comments = "\n- ".join(item.top_comments[:3]) if item.top_comments else "[no HN comments captured]"
    user_prompt = (
        f"Headline: {item.title}\n"
        f"URL: {item.url}\n"
        f"Author: {item.author}\n"
        f"Score: {item.score}\n"
        f"Relevance score: {item.relevance_score}\n"
        f"Article fetch status: {item.article_fetch_status}\n\n"
        f"Article text (primary source of truth):\n{article_text}\n\n"
        f"HN story text (secondary, may be empty):\n{item.text or '[empty]'}\n\n"
        f"HN comments (secondary discussion context, not source of truth):\n- {comments}\n\n"
        "Write a concise entry in plain prose. Base factual summary primarily on article text. "
        "Use HN comments only for caveats, reactions, or extra context. If article text is unavailable, say so implicitly by keeping claims modest and grounded in HN context."
    )
    body = generate_text(settings.ai, HN_SYSTEM_PROMPT, user_prompt)
    return {
        "hn_id": item.hn_id,
        "headline": item.title,
        "title": item.title,
        "url": item.url,
        "body": body,
        "source_version": "article-v1",
    }


def _format_run_folder_name(settings: Settings, run_date: str) -> str:
    return datetime.strptime(run_date, "%Y-%m-%d").strftime(settings.output.folder_date_format)


def _sanitize_remote_path(path: str) -> str:
    trimmed = path.strip()
    if not trimmed or trimmed == "/":
        return "/"
    normalized = trimmed if trimmed.startswith("/") else f"/{trimmed}"
    return normalized.rstrip("/") or "/"


def _normalize_crosspoint_host(host: str) -> str:
    trimmed = host.strip()
    if not trimmed:
        return "http://crosspoint.local"
    if trimmed.startswith(("http://", "https://")):
        return trimmed.rstrip("/")
    return f"http://{trimmed.rstrip('/')}"


def _crosspoint_candidate_hosts(settings: Settings) -> list[str]:
    hosts = [settings.delivery.host, *settings.delivery.fallback_hosts]
    deduped: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        normalized = _normalize_crosspoint_host(host)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(host)
    return deduped


def _crosspoint_remote_dir(settings: Settings, run_date: str) -> str:
    base_path = _sanitize_remote_path(settings.delivery.remote_base_path)
    folder_name = _format_run_folder_name(settings, run_date)
    if base_path == "/":
        return f"/{folder_name}"
    return f"{base_path}/{folder_name}"


def _delivery_target_summary(settings: Settings) -> str:
    if settings.delivery.method == "crosspoint":
        return f"{_normalize_crosspoint_host(settings.delivery.host)}{_sanitize_remote_path(settings.delivery.remote_base_path)}/<dd-mm-yyyy>"
    return str(settings.delivery.target_path)


def _ping_crosspoint(host: str) -> None:
    response = requests.get(f"{_normalize_crosspoint_host(host)}/api/status", timeout=15)
    response.raise_for_status()


def _crosspoint_list_dir(host: str, remote_dir: str) -> list[dict]:
    response = requests.get(
        f"{_normalize_crosspoint_host(host)}/api/files",
        params={"path": remote_dir},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected /api/files response for {remote_dir}: {payload!r}")
    return payload


def _crosspoint_create_folder(host: str, parent_dir: str, folder_name: str) -> None:
    response = requests.post(
        f"{_normalize_crosspoint_host(host)}/mkdir",
        data={"name": folder_name, "path": parent_dir},
        timeout=30,
    )
    if not response.ok:
        body = response.text.strip()
        raise RuntimeError(body or f"mkdir failed with HTTP {response.status_code}")


def _ensure_crosspoint_remote_dir(host: str, remote_dir: str) -> None:
    normalized_dir = _sanitize_remote_path(remote_dir)
    if normalized_dir == "/":
        return

    current_dir = "/"
    for segment in [part for part in normalized_dir.strip("/").split("/") if part]:
        entries = _crosspoint_list_dir(host, current_dir)
        existing = next((item for item in entries if item.get("name") == segment), None)
        next_dir = f"/{segment}" if current_dir == "/" else f"{current_dir}/{segment}"

        if existing is None:
            LOGGER.info("Creating remote folder %s on %s", next_dir, _normalize_crosspoint_host(host))
            _crosspoint_create_folder(host, current_dir, segment)
            LOGGER.info("Created remote folder %s on %s", next_dir, _normalize_crosspoint_host(host))
        elif not existing.get("isDirectory"):
            raise RuntimeError(f"remote path segment exists as a file, not a folder: {next_dir}")

        current_dir = next_dir


def _upload_crosspoint_artifact(host: str, remote_dir: str, artifact: Path) -> str:
    with artifact.open("rb") as handle:
        response = requests.post(
            f"{_normalize_crosspoint_host(host)}/upload",
            params={"path": remote_dir},
            files={"file": (artifact.name, handle, "application/epub+zip")},
            timeout=300,
        )
    if not response.ok:
        body = response.text.strip()
        raise RuntimeError(body or f"upload failed with HTTP {response.status_code}")
    return response.text.strip() or "OK"


def _deliver_via_crosspoint(
    settings: Settings,
    run_date: str,
    artifacts: list[Path],
    run_id: int | None = None,
) -> StageResult:
    remote_dir = _crosspoint_remote_dir(settings, run_date)
    candidate_hosts = _crosspoint_candidate_hosts(settings)
    delivered_names: set[str] = set()
    delivered_files: list[str] = []
    attempt = 0
    deadline = None
    if settings.delivery.retry_timeout_minutes > 0:
        deadline = time.monotonic() + (settings.delivery.retry_timeout_minutes * 60)
    retry_interval = max(settings.delivery.retry_interval_seconds, 1)
    recent_errors: list[str] = []

    LOGGER.info(
        "Starting CrossPoint delivery to %s via hosts %s for %d artifact(s)",
        remote_dir,
        ", ".join(_normalize_crosspoint_host(host) for host in candidate_hosts),
        len(artifacts),
    )

    while len(delivered_names) < len(artifacts):
        attempt += 1
        LOGGER.info(
            "Delivery attempt %d starting; %d/%d artifact(s) delivered so far",
            attempt,
            len(delivered_names),
            len(artifacts),
        )
        for host in candidate_hosts:
            remaining = [artifact for artifact in artifacts if artifact.name not in delivered_names]
            if not remaining:
                break
            normalized_host = _normalize_crosspoint_host(host)
            try:
                LOGGER.info(
                    "Checking device availability at %s for %d remaining artifact(s)",
                    normalized_host,
                    len(remaining),
                )
                _ping_crosspoint(host)
                LOGGER.info("CrossPoint host %s responded successfully", normalized_host)
                _ensure_crosspoint_remote_dir(host, remote_dir)
                for artifact in remaining:
                    LOGGER.info(
                        "Uploading %s (%d bytes) to %s%s",
                        artifact.name,
                        artifact.stat().st_size,
                        normalized_host,
                        remote_dir,
                    )
                    response_text = _upload_crosspoint_artifact(host, remote_dir, artifact)
                    remote_path = f"{normalized_host}{remote_dir}/{artifact.name}"
                    delivered_names.add(artifact.name)
                    delivered_files.append(remote_path)
                    LOGGER.info("Uploaded %s to %s (%s)", artifact.name, remote_path, response_text)
                    if run_id is not None:
                        record_artifact(
                            settings.runtime.db_path,
                            run_id,
                            artifact.name,
                            remote_path,
                            "success",
                            size_bytes=artifact.stat().st_size,
                        )
            except Exception as exc:  # noqa: BLE001
                recent_errors.append(f"{normalized_host}: {exc}")
                LOGGER.warning("Delivery attempt via %s failed: %s", normalized_host, exc)
                continue

        if len(delivered_names) == len(artifacts):
            LOGGER.info(
                "CrossPoint delivery finished after %d attempt(s); delivered %d artifact(s)",
                attempt,
                len(delivered_files),
            )
            return StageResult(
                stage="deliver_to_xteink",
                status="success",
                details={
                    "delivered_files": delivered_files,
                    "target_dir": remote_dir,
                    "delivery_host_candidates": [_normalize_crosspoint_host(host) for host in candidate_hosts],
                    "attempts": attempt,
                },
            )

        if deadline is not None and time.monotonic() >= deadline:
            error_summary = "; ".join(recent_errors[-max(len(candidate_hosts), 1) * 3 :]) or "device unreachable"
            raise RuntimeError(
                f"could not deliver to CrossPoint path {remote_dir} after {attempt} attempt(s): {error_summary}"
            )

        LOGGER.info(
            "Delivery incomplete after attempt %d; sleeping %d second(s) before retry",
            attempt,
            retry_interval,
        )
        time.sleep(retry_interval)


def load_config_stage(project_root: Path, config_path: Path) -> tuple[Settings, StageResult]:
    LOGGER.info("Loading config from %s", config_path)
    settings = load_settings(project_root, config_path)
    ensure_runtime_directories(settings)
    ensure_database(settings.runtime.db_path)
    seed_publications(settings.runtime.db_path, settings.substack_publications)
    active_playlist_ids = [playlist.playlist_id for playlist in settings.youtube.playlists if playlist.active]
    LOGGER.info(
        "Loaded config: %d publication(s), %d playlist(s), delivery=%s",
        len(settings.substack_publications),
        len(settings.youtube.playlists),
        _delivery_target_summary(settings),
    )
    result = StageResult(
        stage="load_config",
        status="success",
        details={
            "timezone": settings.timezone,
            "schedule": settings.schedule,
            "publication_count": len(settings.substack_publications),
            "playlist_count": len(settings.youtube.playlists),
            "active_playlist_ids": active_playlist_ids,
            "hn_candidate_count": settings.hackernews.candidate_count,
            "delivery_method": settings.delivery.method,
            "delivery_target": _delivery_target_summary(settings),
        },
    )
    return settings, result


def _substack_published_at(post: SubstackPost) -> datetime:
    try:
        parsed = parsedate_to_datetime(post.published_at)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _latest_post_per_publication(posts: list[SubstackPost]) -> list[SubstackPost]:
    publication_order: list[str] = []
    latest_by_publication: dict[str, SubstackPost] = {}
    for post in posts:
        publication = post.publication_name
        if publication not in latest_by_publication:
            publication_order.append(publication)
            latest_by_publication[publication] = post
            continue
        if _substack_published_at(post) > _substack_published_at(latest_by_publication[publication]):
            latest_by_publication[publication] = post
    return [latest_by_publication[publication] for publication in publication_order]


def ingest_substack_stage(settings: Settings, run_date: str) -> StageResult:
    staging_dir = run_staging_dir(settings, run_date)
    LOGGER.info("Starting Substack ingestion for %s", run_date)
    known_urls = existing_substack_urls(settings.runtime.db_path)
    unseen_posts = [post for post in fetch_new_posts(settings.substack_publications) if post.post_url not in known_urls]
    posts = _latest_post_per_publication(unseen_posts)
    hashes = {post.post_url: content_hash(post.body_html) for post in posts}
    save_substack_posts(settings.runtime.db_path, posts, run_id=0, content_hashes=hashes)
    payload = [asdict(post) for post in posts]
    _write_json(staging_dir / "substack_posts.json", payload)
    LOGGER.info("Substack ingestion complete: %d new post(s) written to %s", len(posts), staging_dir / "substack_posts.json")
    return StageResult(
        stage="ingest_substack",
        status="success",
        details={"new_posts": len(posts), "staging_file": str(staging_dir / "substack_posts.json")},
    )


def ingest_youtube_stage(settings: Settings, run_date: str) -> StageResult:
    staging_dir = run_staging_dir(settings, run_date)
    LOGGER.info("Starting YouTube ingestion for %s", run_date)
    processed_video_ids = existing_youtube_article_ids(settings.runtime.db_path)
    staged_video_ids: set[str] = set()
    selected: list[tuple[str, YoutubeVideo]] = []
    active_playlists = [playlist for playlist in settings.youtube.playlists if playlist.active]

    for playlist in active_playlists:
        LOGGER.info("Fetching playlist %s", playlist.playlist_id)
        candidate_limit = settings.youtube.max_videos_per_day * YOUTUBE_PLAYLIST_CANDIDATE_MULTIPLIER
        playlist_videos = fetch_playlist_videos(playlist.playlist_id, candidate_limit)
        for video in playlist_videos:
            if video.video_id in processed_video_ids or video.video_id in staged_video_ids:
                continue
            selected.append((playlist.playlist_id, video))
            staged_video_ids.add(video.video_id)
            if len(selected) >= settings.youtube.max_videos_per_day:
                break
        if len(selected) >= settings.youtube.max_videos_per_day:
            break

    videos = [video for _, video in selected]
    by_playlist: dict[str, list[YoutubeVideo]] = {}
    for playlist_id, video in selected:
        by_playlist.setdefault(playlist_id, []).append(video)
    for playlist_id, playlist_videos in by_playlist.items():
        save_youtube_items(settings.runtime.db_path, playlist_id, playlist_videos, run_id=0)
    _write_json(staging_dir / "youtube_videos.json", [asdict(video) for video in videos])
    transcript_ready = sum(1 for video in videos if video.transcript_status == "available")
    LOGGER.info(
        "YouTube ingestion complete: %d new video(s), %d transcript-ready, staging=%s",
        len(videos),
        transcript_ready,
        staging_dir / "youtube_videos.json",
    )
    return StageResult(
        stage="ingest_youtube_playlist",
        status="success",
        details={
            "new_videos": len(videos),
            "transcript_ready": transcript_ready,
            "active_playlists": len(active_playlists),
            "staging_file": str(staging_dir / "youtube_videos.json"),
        },
    )


def ingest_hn_stage(settings: Settings, run_date: str) -> StageResult:
    staging_dir = run_staging_dir(settings, run_date)
    LOGGER.info("Starting Hacker News ingestion for %s", run_date)
    candidates = fetch_candidate_items(settings.hackernews.candidate_count)
    personality = load_hn_personality(settings.hackernews.personality_file)
    ranked = rank_items(
        candidates,
        settings.hackernews.final_count,
        min_relevance_score=settings.hackernews.min_relevance_score,
        min_selected_items=settings.hackernews.min_selected_items,
        personality=personality,
    )
    save_hn_items(settings.runtime.db_path, ranked, run_id=0)
    _write_json(staging_dir / "hn_items.json", [asdict(item) for item in ranked])
    LOGGER.info(
        "Hacker News ingestion complete: %d candidate(s), %d selected, staging=%s",
        len(candidates),
        len(ranked),
        staging_dir / "hn_items.json",
    )
    return StageResult(
        stage="ingest_hn",
        status="success",
        details={"selected_items": len(ranked), "staging_file": str(staging_dir / "hn_items.json")},
    )


def compose_daily_pack_stage(settings: Settings, run_date: str) -> StageResult:
    staging_dir = run_staging_dir(settings, run_date)
    artifact_dir = run_artifact_dir(settings, run_date)
    created: list[str] = []
    ai_generated_files: list[str] = []
    LOGGER.info("Starting daily pack composition for %s into %s", run_date, artifact_dir)

    substack_entries = _read_json(staging_dir / "substack_posts.json")
    if substack_entries:
        path = render_substack_book(
            f"Content Hub Substack - {run_date}",
            artifact_dir / "01-substack.epub",
            substack_entries,
        )
        created.append(str(path))
        LOGGER.info("Rendered Substack EPUB at %s", path)

    youtube_raw = _read_json(staging_dir / "youtube_videos.json")
    transcript_ready = [YoutubeVideo(**item) for item in youtube_raw if item.get("transcript_status") == "available"]
    if transcript_ready:
        video_notes_path = staging_dir / "video_notes.json"
        video_notes_raw = _read_json(video_notes_path)
        existing_video_note_ids = {item.get("video_id") for item in video_notes_raw if item.get("video_id")}
        missing_videos = [video for video in transcript_ready if video.video_id not in existing_video_note_ids]
        if missing_videos:
            LOGGER.info("Generating AI video notes for %d transcript-ready video(s)", len(missing_videos))
            for video in missing_videos:
                video_notes_raw.append(_video_entry(settings, video))
                _write_json(video_notes_path, video_notes_raw)
            ai_generated_files.append(str(video_notes_path))
            LOGGER.info("Wrote AI video notes to %s", video_notes_path)
        video_entries = [
            entry
            for entry in (
                _normalize_text_entry(item, ("article_title", "title"), ("body", "summary", "content"))
                for item in video_notes_raw
            )
            if entry is not None
        ]
        if video_entries:
            source_videos = {video.video_id: video for video in transcript_ready}
            save_youtube_articles(
                settings.runtime.db_path,
                [
                    (
                        source_videos[item["video_id"]],
                        item.get("article_title", item.get("title", source_videos[item["video_id"]].title)),
                        item["body"],
                    )
                    for item in video_notes_raw
                    if item.get("video_id") in source_videos and item.get("body")
                ],
                run_id=0,
            )
            path = render_text_book(
                f"Content Hub Video Notes - {run_date}",
                artifact_dir / "02-video-notes.epub",
                video_entries,
                source_key="url",
            )
            created.append(str(path))
            LOGGER.info("Rendered video notes EPUB at %s", path)

    hn_raw = _read_json(staging_dir / "hn_items.json")
    hn_items = [HackerNewsItem(**item) for item in hn_raw]
    if hn_items:
        hn_entries_path = staging_dir / "hn_brief_entries.json"
        hn_entries_raw = _read_json(hn_entries_path)
        expected_hn_ids = [item.hn_id for item in hn_items[: settings.hackernews.final_count]]
        if hn_entries_raw and not _hn_entries_current(hn_entries_raw, expected_hn_ids):
            LOGGER.info("Existing HN brief entries are stale; regenerating %s", hn_entries_path)
            hn_entries_raw = []
        if not hn_entries_raw:
            LOGGER.info("Generating AI HN brief entries for %d item(s)", min(len(hn_items), settings.hackernews.final_count))
            hn_entries_raw = [_hn_entry(settings, item) for item in hn_items[: settings.hackernews.final_count]]
            _write_json(hn_entries_path, hn_entries_raw)
            ai_generated_files.append(str(hn_entries_path))
            LOGGER.info("Wrote AI HN brief entries to %s", hn_entries_path)
        hn_entries = [
            entry
            for entry in (
                _normalize_text_entry(item, ("headline", "title"), ("body", "brief", "summary"))
                for item in hn_entries_raw
            )
            if entry is not None
        ]
        if hn_entries:
            path = render_text_book(
                f"Content Hub Hacker News Brief - {run_date}",
                artifact_dir / "03-hn-brief.epub",
                hn_entries,
                source_key="url",
            )
            created.append(str(path))
            LOGGER.info("Rendered Hacker News brief EPUB at %s", path)

    LOGGER.info(
        "Composition finished with %d artifact(s); AI-generated files: %d",
        len(created),
        len(ai_generated_files),
    )

    return StageResult(
        stage="compose_daily_pack",
        status="success" if created else "degraded",
        details={
            "artifacts": created,
            "artifact_dir": str(artifact_dir),
            "ai_generated_files": ai_generated_files,
        },
    )


def _send_telegram_fallback(message: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return
    requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=30,
    )


def deliver_to_xteink_stage(settings: Settings, run_date: str, run_id: int | None = None) -> StageResult:
    artifact_dir = run_artifact_dir(settings, run_date)
    artifacts = sorted(path for path in artifact_dir.glob("*") if path.is_file())
    LOGGER.info("Starting delivery for %s from artifact dir %s", run_date, artifact_dir)
    if not artifacts:
        raise RuntimeError(f"no artifacts found in {artifact_dir}")
    if settings.delivery.method == "local_only":
        if run_id is not None:
            for artifact in artifacts:
                record_artifact(settings.runtime.db_path, run_id, artifact.name, artifact, "local_only")
        LOGGER.info("Delivery method is local_only; %d artifact(s) kept locally in %s", len(artifacts), artifact_dir)
        return StageResult(
            stage="deliver_to_xteink",
            status="success",
            details={"delivered_files": [str(path) for path in artifacts], "target_dir": str(artifact_dir)},
        )
    if settings.delivery.method == "crosspoint":
        return _deliver_via_crosspoint(settings, run_date, artifacts, run_id=run_id)
    target_dir = settings.delivery.target_path
    if target_dir is None:
        raise RuntimeError("delivery target path is not configured")
    if not target_dir.exists():
        raise RuntimeError(f"delivery target does not exist: {target_dir}")

    delivered: list[str] = []
    for artifact in artifacts:
        destination = target_dir / artifact.name
        LOGGER.info("Copying %s to %s", artifact, destination)
        shutil.copy2(artifact, destination)
        if settings.delivery.verify_copy:
            if not destination.exists():
                raise RuntimeError(f"copy verification failed for {destination}")
            if destination.stat().st_size != artifact.stat().st_size:
                raise RuntimeError(f"size mismatch for {destination}")
        delivered.append(str(destination))
        if run_id is not None:
            record_artifact(settings.runtime.db_path, run_id, artifact.name, destination, "success")
    LOGGER.info("Filesystem delivery finished to %s", target_dir)

    return StageResult(
        stage="deliver_to_xteink",
        status="success",
        details={"delivered_files": delivered, "target_dir": str(target_dir)},
    )


def run_pipeline(project_root: Path, config_path: Path, run_date: str | None = None, retry_only_if_needed: bool = False) -> list[StageResult]:
    settings, config_result = load_config_stage(project_root, config_path)
    today = run_date or _now(settings).date().isoformat()
    LOGGER.info("Starting pipeline run for %s", today)

    if retry_only_if_needed:
        status = latest_run_status(settings.runtime.db_path, today)
        if status == "success":
            LOGGER.info("Skipping pipeline for %s because the latest run already succeeded", today)
            return [StageResult(stage="run_pipeline", status="skipped", details={"reason": "latest run already succeeded"})]

    run_id = start_run(settings.runtime.db_path, today, _now(settings).isoformat())
    results = [config_result]
    degraded_messages: list[str] = []

    try:
        stages = [
            ("ingest_substack", lambda: ingest_substack_stage(settings, today)),
            ("ingest_youtube_playlist", lambda: ingest_youtube_stage(settings, today)),
            ("ingest_hn", lambda: ingest_hn_stage(settings, today)),
            ("compose_daily_pack", lambda: compose_daily_pack_stage(settings, today)),
            ("deliver_to_xteink", lambda: deliver_to_xteink_stage(settings, today, run_id=run_id)),
        ]
        for stage_name, stage_fn in stages:
            try:
                LOGGER.info("Running stage: %s", stage_name)
                results.append(stage_fn())
                LOGGER.info("Finished stage: %s", stage_name)
            except Exception as exc:  # noqa: BLE001
                degraded_messages.append(str(exc))
                LOGGER.exception("Stage failed: %s", stage_name)
                results.append(StageResult(stage=stage_name, status="failed", details={"error": str(exc)}))
                if stage_name == "deliver_to_xteink":
                    raise
        overall_status = "success" if any(result.stage == "compose_daily_pack" and result.status == "success" for result in results) else "degraded"
        finish_run(settings.runtime.db_path, run_id, _now(settings).isoformat(), overall_status, "; ".join(degraded_messages))
        LOGGER.info("Pipeline finished for %s with status=%s", today, overall_status)
    except Exception as exc:  # noqa: BLE001
        finish_run(settings.runtime.db_path, run_id, _now(settings).isoformat(), "failed", str(exc))
        LOGGER.exception("Pipeline failed for %s", today)
        _send_telegram_fallback(f"Content Hub Pack failed on {today}: {exc}")
        raise

    return results
