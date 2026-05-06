from pathlib import Path

from content_hub_pack.config import load_settings, run_artifact_dir


def test_load_settings_resolves_relative_paths(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    sources_dir = config_dir / "sources"
    sources_dir.mkdir()
    (sources_dir / "substacks.json").write_text(
        """
{
  "publications": [
    {
      "name": "Example",
      "base_url": "https://example.substack.com",
      "feed_url": "https://example.substack.com/feed",
      "priority": 1,
      "active": true
    }
  ]
}
""".strip()
    )
    (sources_dir / "youtube_playlists.json").write_text(
        """
{
  "playlists": [
    {
      "name": "Primary",
      "playlist_id": "PL123",
      "active": true
    }
  ]
}
""".strip()
    )
    (config_dir / "config.yaml").write_text(
        """
timezone: Europe/Paris
schedule: "30 6 * * *"
substack:
  source_file: ./config/sources/substacks.json
youtube:
  source_file: ./config/sources/youtube_playlists.json
  max_videos_per_day: 2
hackernews:
  candidate_count: 30
  final_count: 10
  min_relevance_score: 14.0
  min_selected_items: 3
delivery:
  method: local_only
  target_path: .
output:
  root_dir: .
  folder_prefix: output
  folder_date_format: "%d-%m-%Y"
runtime:
  db_path: ./data/runtime/db.sqlite3
  staging_dir: ./data/staging
ai:
  provider: openrouter
  model: nvidia/nemotron-3-super-120b-a12b:free
  base_url: https://openrouter.ai/api/v1
"""
    )

    settings = load_settings(tmp_path, config_dir / "config.yaml")

    assert settings.delivery.target_path == tmp_path.resolve()
    assert settings.output.root_dir == tmp_path.resolve()
    assert settings.runtime.db_path == (tmp_path / "data/runtime/db.sqlite3").resolve()
    assert settings.ai.base_url == "https://openrouter.ai/api/v1"
    assert settings.hackernews.min_relevance_score == 14.0
    assert settings.hackernews.min_selected_items == 3
    assert len(settings.substack_publications) == 1
    assert settings.youtube.playlists[0].playlist_id == "PL123"


def test_run_artifact_dir_uses_output_dd_mm_yyyy(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        """
delivery:
  method: local_only
  target_path: .
output:
  root_dir: .
  folder_prefix: output
  folder_date_format: "%d-%m-%Y"
runtime:
  db_path: ./data/runtime/db.sqlite3
  staging_dir: ./data/staging
youtube:
  playlists:
    - name: Primary
      playlist_id: PL123
      active: true
hackernews:
  candidate_count: 30
  final_count: 10
  min_relevance_score: 14.0
  min_selected_items: 3
ai:
  provider: openrouter
  model: nvidia/nemotron-3-super-120b-a12b:free
  base_url: https://openrouter.ai/api/v1
substack:
  publications: []
"""
    )

    settings = load_settings(tmp_path, config_dir / "config.yaml")
    artifact_dir = run_artifact_dir(settings, "2026-04-23")

    assert artifact_dir == (tmp_path / "output_23-04-2026").resolve()


def test_load_settings_supports_crosspoint_delivery(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        """
delivery:
  method: crosspoint
  host: crosspoint.local
  fallback_hosts:
    - 10.0.0.2
  remote_base_path: /Daily
  verify_copy: true
  retry_interval_seconds: 300
  retry_timeout_minutes: 0
output:
  root_dir: .
  folder_prefix: output
  folder_date_format: "%d-%m-%Y"
runtime:
  db_path: ./data/runtime/db.sqlite3
  staging_dir: ./data/staging
youtube:
  playlists: []
hackernews:
  candidate_count: 30
  final_count: 10
  min_relevance_score: 14.0
  min_selected_items: 3
ai:
  provider: openrouter
  model: nvidia/nemotron-3-super-120b-a12b:free
  base_url: https://openrouter.ai/api/v1
substack:
  publications: []
"""
    )

    settings = load_settings(tmp_path, config_dir / "config.yaml")

    assert settings.delivery.method == "crosspoint"
    assert settings.delivery.target_path is None
    assert settings.delivery.host == "crosspoint.local"
    assert settings.delivery.fallback_hosts == ["10.0.0.2"]
    assert settings.delivery.remote_base_path == "/Daily"
    assert settings.delivery.retry_interval_seconds == 300
    assert settings.delivery.retry_timeout_minutes == 0
    assert settings.hackernews.min_selected_items == 3
