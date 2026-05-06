from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PublicationConfig:
    name: str
    base_url: str
    feed_url: str
    priority: int
    active: bool = True


@dataclass(slots=True)
class PlaylistConfig:
    name: str
    playlist_id: str
    active: bool = True


@dataclass(slots=True)
class YoutubeConfig:
    playlists: list[PlaylistConfig]
    max_videos_per_day: int = 4


@dataclass(slots=True)
class HackerNewsConfig:
    candidate_count: int = 30
    final_count: int = 10
    min_relevance_score: float = 14.0
    min_selected_items: int = 3
    personality_file: Path | None = None


@dataclass(slots=True)
class DeliveryConfig:
    method: str
    target_path: Path | None = None
    verify_copy: bool = True
    fallback_notify: str = "telegram"
    host: str = "crosspoint.local"
    fallback_hosts: list[str] = field(default_factory=list)
    remote_base_path: str = "/Daily"
    retry_interval_seconds: int = 300
    retry_timeout_minutes: int = 0


@dataclass(slots=True)
class OutputConfig:
    root_dir: Path
    folder_prefix: str = "output"
    folder_date_format: str = "%d-%m-%Y"


@dataclass(slots=True)
class RuntimeConfig:
    db_path: Path
    staging_dir: Path


@dataclass(slots=True)
class AIConfig:
    provider: str
    model: str
    base_url: str


@dataclass(slots=True)
class Settings:
    project_root: Path
    timezone: str
    schedule: str
    substack_publications: list[PublicationConfig]
    youtube: YoutubeConfig
    hackernews: HackerNewsConfig
    delivery: DeliveryConfig
    output: OutputConfig
    runtime: RuntimeConfig
    ai: AIConfig


@dataclass(slots=True)
class SubstackPost:
    publication_name: str
    post_url: str
    title: str
    author: str
    published_at: str
    body_html: str


@dataclass(slots=True)
class YoutubeVideo:
    video_id: str
    title: str
    channel: str
    published_at: str
    duration_seconds: int
    url: str
    transcript_status: str
    transcript_text: str
    description: str = ""


@dataclass(slots=True)
class HackerNewsItem:
    hn_id: int
    title: str
    url: str
    author: str
    score: int
    created_at: str
    text: str
    top_comments: list[str]
    article_text: str = ""
    article_fetch_status: str = "not_attempted"
    relevance_score: float = 0.0


@dataclass(slots=True)
class StageResult:
    stage: str
    status: str
    details: dict[str, Any]
