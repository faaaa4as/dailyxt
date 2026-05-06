from __future__ import annotations

import json
from pathlib import Path

from content_hub_pack.config import load_settings
from content_hub_pack.models import (
    AIConfig,
    DeliveryConfig,
    HackerNewsConfig,
    HackerNewsItem,
    OutputConfig,
    RuntimeConfig,
    Settings,
    YoutubeConfig,
)
from content_hub_pack.ranking import HackerNewsPersonality, load_hn_personality, rank_items, relevance_score


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        timezone="Europe/Paris",
        schedule="30 6 * * *",
        substack_publications=[],
        youtube=YoutubeConfig(playlists=[]),
        hackernews=HackerNewsConfig(final_count=2, min_relevance_score=0, min_selected_items=1),
        delivery=DeliveryConfig(method="local_only", target_path=tmp_path),
        output=OutputConfig(root_dir=tmp_path),
        runtime=RuntimeConfig(db_path=tmp_path / "data/runtime/test.sqlite3", staging_dir=tmp_path / "data/staging"),
        ai=AIConfig(provider="openrouter", model="test", base_url="https://example.com"),
    )


def test_load_settings_accepts_hackernews_personality_file(tmp_path: Path) -> None:
    (tmp_path / "config/personality").mkdir(parents=True)
    (tmp_path / "config/personality/hackernews.md").write_text("# profile\n")
    config = tmp_path / "config/config.yaml"
    config.write_text(
        """
timezone: Europe/Paris
substack: {}
youtube: {}
hackernews:
  candidate_count: 30
  final_count: 10
  min_relevance_score: 14.0
  min_selected_items: 3
  personality_file: ./config/personality/hackernews.md
delivery:
  method: local_only
runtime:
  db_path: ./data/runtime/content_hub_pack.sqlite3
  staging_dir: ./data/staging
output:
  root_dir: .
ai:
  provider: openrouter
  model: test
  base_url: https://example.com
"""
    )

    settings = load_settings(tmp_path, config)

    assert settings.hackernews.personality_file == tmp_path / "config/personality/hackernews.md"


def test_hn_personality_markdown_schema_drives_title_ranking(tmp_path: Path) -> None:
    profile_path = tmp_path / "hackernews.md"
    profile_path.write_text(
        """
# Hacker News personality

```content-hub-personality
{
  "schema_version": 1,
  "briefing_context": "Prefer industrial design and robotics.",
  "positive_keywords": {"robotics": 8.0, "industrial design": 5.0},
  "negative_keywords": {"crypto": -8.0}
}
```
"""
    )
    personality = load_hn_personality(profile_path)
    robotics = HackerNewsItem(
        hn_id=1,
        title="New robotics controller for industrial design labs",
        url="https://example.com/robotics",
        author="a",
        score=20,
        created_at="2026-05-05T00:00:00Z",
        text="",
        top_comments=[],
    )
    crypto = HackerNewsItem(
        hn_id=2,
        title="Crypto trading market infrastructure",
        url="https://example.com/crypto",
        author="b",
        score=2000,
        created_at="2026-05-05T00:00:00Z",
        text="",
        top_comments=[],
    )

    assert personality.briefing_context == "Prefer industrial design and robotics."
    assert relevance_score(robotics, personality) > relevance_score(crypto, personality)


def test_rank_items_accepts_personality_object() -> None:
    personality = HackerNewsPersonality(
        positive_keywords={"weird machines": 10.0},
        negative_keywords={"ai": -10.0},
        briefing_context="Prefer weird machines over AI hype.",
    )
    weird = HackerNewsItem(1, "Weird machines in old terminals", "https://example.com/1", "a", 10, "now", "", [])
    ai = HackerNewsItem(2, "AI agents for coding", "https://example.com/2", "b", 1000, "now", "", [])

    ranked = rank_items([ai, weird], final_count=2, min_relevance_score=0, personality=personality)

    assert [item.hn_id for item in ranked] == [1, 2]
