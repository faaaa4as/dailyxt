from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

import yaml
from dotenv import load_dotenv

from content_hub_pack.models import (
    AIConfig,
    DeliveryConfig,
    HackerNewsConfig,
    OutputConfig,
    PlaylistConfig,
    PublicationConfig,
    RuntimeConfig,
    Settings,
    YoutubeConfig,
)


def _resolve_path(project_root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (project_root / candidate).resolve()


def _load_substack_publications(project_root: Path, substack_raw: dict) -> list[PublicationConfig]:
    source_file = substack_raw.get("source_file")
    if source_file:
        source_path = _resolve_path(project_root, source_file)
        payload = json.loads(source_path.read_text()) if source_path.exists() else {}
        publications = payload.get("publications", [])
    else:
        publications = substack_raw.get("publications", [])
    return [PublicationConfig(**item) for item in publications]


def _load_youtube_playlists(project_root: Path, youtube_raw: dict) -> list[PlaylistConfig]:
    source_file = youtube_raw.get("source_file")
    if source_file:
        source_path = _resolve_path(project_root, source_file)
        payload = json.loads(source_path.read_text()) if source_path.exists() else {}
        playlists = payload.get("playlists", [])
    elif youtube_raw.get("playlists"):
        playlists = youtube_raw.get("playlists", [])
    elif youtube_raw.get("playlist_id"):
        playlists = [
            {
                "name": "Primary Playlist",
                "playlist_id": youtube_raw["playlist_id"],
                "active": True,
            }
        ]
    else:
        playlists = []
    return [PlaylistConfig(**item) for item in playlists]


def load_settings(project_root: Path, config_path: Path) -> Settings:
    load_dotenv(project_root / ".env", override=False)
    load_dotenv(project_root / "config" / ".env", override=False)

    raw = yaml.safe_load(config_path.read_text()) or {}

    substack_raw = raw.get("substack", {})
    youtube_raw = raw.get("youtube", {})
    substack_publications = _load_substack_publications(project_root, substack_raw)
    youtube = YoutubeConfig(
        playlists=_load_youtube_playlists(project_root, youtube_raw),
        max_videos_per_day=youtube_raw.get("max_videos_per_day", 4),
    )
    hackernews_raw = dict(raw.get("hackernews", {}))
    if hackernews_raw.get("personality_file"):
        hackernews_raw["personality_file"] = _resolve_path(project_root, hackernews_raw["personality_file"])
    hackernews = HackerNewsConfig(**hackernews_raw)
    delivery_raw = raw.get("delivery", {})
    output_raw = raw.get("output", {})
    runtime_raw = raw.get("runtime", {})
    ai_raw = raw.get("ai", {})

    delivery_method = delivery_raw.get("method", "local_only")
    target_path_raw = delivery_raw.get("target_path")
    if delivery_method not in {"local_only", "crosspoint"} and not target_path_raw:
        raise ValueError("delivery.target_path is required")

    settings = Settings(
        project_root=project_root.resolve(),
        timezone=raw.get("timezone", "Europe/Paris"),
        schedule=raw.get("schedule", "30 6 * * *"),
        substack_publications=substack_publications,
        youtube=youtube,
        hackernews=hackernews,
        delivery=DeliveryConfig(
            method=delivery_method,
            target_path=_resolve_path(project_root, target_path_raw or ".") if target_path_raw or delivery_method == "local_only" else None,
            verify_copy=bool(delivery_raw.get("verify_copy", True)),
            fallback_notify=delivery_raw.get("fallback_notify", "telegram"),
            host=delivery_raw.get("host", "crosspoint.local"),
            fallback_hosts=list(delivery_raw.get("fallback_hosts", [])),
            remote_base_path=delivery_raw.get("remote_base_path", "/Daily"),
            retry_interval_seconds=int(delivery_raw.get("retry_interval_seconds", 300)),
            retry_timeout_minutes=int(delivery_raw.get("retry_timeout_minutes", 0)),
        ),
        output=OutputConfig(
            root_dir=_resolve_path(project_root, output_raw.get("root_dir", ".")),
            folder_prefix=output_raw.get("folder_prefix", "output"),
            folder_date_format=output_raw.get("folder_date_format", "%d-%m-%Y"),
        ),
        runtime=RuntimeConfig(
            db_path=_resolve_path(
                project_root,
                runtime_raw.get("db_path", "./data/runtime/content_hub_pack.sqlite3"),
            ),
            staging_dir=_resolve_path(
                project_root,
                runtime_raw.get("staging_dir", "./data/staging"),
            ),
        ),
        ai=AIConfig(
            provider=ai_raw.get("provider", "openrouter"),
            model=ai_raw.get("model", "nvidia/nemotron-3-super-120b-a12b:free"),
            base_url=ai_raw.get("base_url", "https://openrouter.ai/api/v1"),
        ),
    )
    return settings


def ensure_runtime_directories(settings: Settings) -> None:
    settings.runtime.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.runtime.staging_dir.mkdir(parents=True, exist_ok=True)
    settings.output.root_dir.mkdir(parents=True, exist_ok=True)


def run_artifact_dir(settings: Settings, run_date: str) -> Path:
    formatted = datetime.strptime(run_date, "%Y-%m-%d").strftime(settings.output.folder_date_format)
    path = settings.output.root_dir / f"{settings.output.folder_prefix}_{formatted}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_staging_dir(settings: Settings, run_date: str) -> Path:
    path = settings.runtime.staging_dir / run_date
    path.mkdir(parents=True, exist_ok=True)
    return path
