from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

from content_hub_pack.models import HackerNewsItem, PublicationConfig, SubstackPost, YoutubeVideo


SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS substack_publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL,
    feed_url TEXT NOT NULL,
    priority INTEGER NOT NULL,
    active INTEGER NOT NULL,
    last_seen_published_at TEXT
);

CREATE TABLE IF NOT EXISTS substack_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_name TEXT NOT NULL,
    post_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    published_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    body_html TEXT NOT NULL,
    epub_included_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS youtube_playlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id TEXT NOT NULL,
    video_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    published_at TEXT,
    duration INTEGER NOT NULL,
    transcript_status TEXT NOT NULL,
    processed_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS youtube_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL UNIQUE,
    article_title TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    body_text TEXT NOT NULL,
    epub_included_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS hn_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hn_id INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    author TEXT NOT NULL,
    score INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    text TEXT,
    top_comments_json TEXT NOT NULL,
    article_text TEXT NOT NULL DEFAULT '',
    article_fetch_status TEXT NOT NULL DEFAULT 'not_attempted',
    relevance_score REAL NOT NULL,
    selected_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    delivery_status TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def ensure_database(db_path: Path) -> None:
    with closing(connect(db_path)) as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(
            conn,
            "hn_items",
            {
                "article_text": "TEXT NOT NULL DEFAULT ''",
                "article_fetch_status": "TEXT NOT NULL DEFAULT 'not_attempted'",
            },
        )
        conn.commit()


def seed_publications(db_path: Path, publications: Iterable[PublicationConfig]) -> None:
    with closing(connect(db_path)) as conn:
        for publication in publications:
            conn.execute(
                """
                INSERT INTO substack_publications (name, base_url, feed_url, priority, active)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  base_url = excluded.base_url,
                  feed_url = excluded.feed_url,
                  priority = excluded.priority,
                  active = excluded.active
                """,
                (
                    publication.name,
                    publication.base_url,
                    publication.feed_url,
                    publication.priority,
                    1 if publication.active else 0,
                ),
            )
        conn.commit()


def start_run(db_path: Path, run_date: str, started_at: str) -> int:
    with closing(connect(db_path)) as conn:
        cursor = conn.execute(
            "INSERT INTO pipeline_runs (run_date, started_at, status) VALUES (?, ?, ?)",
            (run_date, started_at, "running"),
        )
        conn.commit()
        return int(cursor.lastrowid)


def finish_run(db_path: Path, run_id: int, ended_at: str, status: str, error_summary: str = "") -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE pipeline_runs
            SET ended_at = ?, status = ?, error_summary = ?
            WHERE id = ?
            """,
            (ended_at, status, error_summary, run_id),
        )
        conn.commit()


def latest_run_status(db_path: Path, run_date: str) -> str | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT status
            FROM pipeline_runs
            WHERE run_date = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (run_date,),
        ).fetchone()
    return row["status"] if row else None


def save_substack_posts(db_path: Path, posts: Iterable[SubstackPost], run_id: int, content_hashes: dict[str, str]) -> None:
    with closing(connect(db_path)) as conn:
        for post in posts:
            conn.execute(
                """
                INSERT INTO substack_posts (
                  publication_name, post_url, title, author, published_at, content_hash, body_html, epub_included_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_url) DO UPDATE SET
                  title = excluded.title,
                  author = excluded.author,
                  published_at = excluded.published_at,
                  content_hash = excluded.content_hash,
                  body_html = excluded.body_html,
                  epub_included_run_id = excluded.epub_included_run_id
                """,
                (
                    post.publication_name,
                    post.post_url,
                    post.title,
                    post.author,
                    post.published_at,
                    content_hashes[post.post_url],
                    post.body_html,
                    run_id,
                ),
            )
        conn.commit()


def save_youtube_items(db_path: Path, playlist_id: str, videos: Iterable[YoutubeVideo], run_id: int) -> None:
    with closing(connect(db_path)) as conn:
        for video in videos:
            conn.execute(
                """
                INSERT INTO youtube_playlist_items (
                  playlist_id, video_id, title, channel, published_at, duration, transcript_status, processed_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                  title = excluded.title,
                  channel = excluded.channel,
                  published_at = excluded.published_at,
                  duration = excluded.duration,
                  transcript_status = excluded.transcript_status,
                  processed_run_id = excluded.processed_run_id
                """,
                (
                    playlist_id,
                    video.video_id,
                    video.title,
                    video.channel,
                    video.published_at,
                    video.duration_seconds,
                    video.transcript_status,
                    run_id,
                ),
            )
        conn.commit()


def save_youtube_articles(db_path: Path, video_articles: list[tuple[YoutubeVideo, str, str]], run_id: int) -> None:
    with closing(connect(db_path)) as conn:
        for video, article_title, body_text in video_articles:
            conn.execute(
                """
                INSERT INTO youtube_articles (
                  video_id, article_title, word_count, content_hash, body_text, epub_included_run_id
                ) VALUES (?, ?, ?, hex(randomblob(16)), ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                  article_title = excluded.article_title,
                  word_count = excluded.word_count,
                  body_text = excluded.body_text,
                  epub_included_run_id = excluded.epub_included_run_id
                """,
                (
                    video.video_id,
                    article_title,
                    len(body_text.split()),
                    body_text,
                    run_id,
                ),
            )
        conn.commit()


def save_hn_items(db_path: Path, items: Iterable[HackerNewsItem], run_id: int) -> None:
    with closing(connect(db_path)) as conn:
        for item in items:
            conn.execute(
                """
                INSERT INTO hn_items (
                  hn_id, title, url, author, score, created_at, text, top_comments_json, article_text, article_fetch_status, relevance_score, selected_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hn_id) DO UPDATE SET
                  title = excluded.title,
                  url = excluded.url,
                  author = excluded.author,
                  score = excluded.score,
                  created_at = excluded.created_at,
                  text = excluded.text,
                  top_comments_json = excluded.top_comments_json,
                  article_text = excluded.article_text,
                  article_fetch_status = excluded.article_fetch_status,
                  relevance_score = excluded.relevance_score,
                  selected_run_id = excluded.selected_run_id
                """,
                (
                    item.hn_id,
                    item.title,
                    item.url,
                    item.author,
                    item.score,
                    item.created_at,
                    item.text,
                    json.dumps(item.top_comments),
                    item.article_text,
                    item.article_fetch_status,
                    item.relevance_score,
                    run_id,
                ),
            )
        conn.commit()


def record_artifact(
    db_path: Path,
    run_id: int,
    artifact_type: str,
    path: Path | str,
    delivery_status: str,
    size_bytes: int | None = None,
) -> None:
    with closing(connect(db_path)) as conn:
        artifact_path = str(path)
        artifact_size = size_bytes if size_bytes is not None else Path(path).stat().st_size
        conn.execute(
            """
            INSERT INTO artifacts (run_id, artifact_type, path, size_bytes, delivery_status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, artifact_type, artifact_path, artifact_size, delivery_status),
        )
        conn.commit()


def existing_substack_urls(db_path: Path) -> set[str]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT post_url FROM substack_posts").fetchall()
    return {row["post_url"] for row in rows}


def existing_video_ids(db_path: Path) -> set[str]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT video_id FROM youtube_playlist_items").fetchall()
    return {row["video_id"] for row in rows}


def existing_youtube_article_ids(db_path: Path) -> set[str]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT video_id FROM youtube_articles").fetchall()
    return {row["video_id"] for row in rows}
