# dailyxt

![cover](dailyxt_cover.png)

OpenRouter-backed daily reading pipeline that:

- converts new Substack posts into a DRM-free EPUB with embedded images
- turns videos from one or more YouTube playlists into article-style reading notes
- ranks Hacker News stories by title and renders concise briefs
- writes finished EPUBs into repo-local folders named `output_DD-MM-YYYY`
- optionally uploads EPUBs to an Xteink/CrossPoint device

## What Is Included

- Python pipeline code
- generic config examples
- generic source JSON files for Substacks and YouTube playlists
- generic Hacker News personality markdown for title ranking
- tests for ingestion, ranking, rendering, delivery, and provider errors
- Rust/Ratatui TUI for managing sources and HN ranking personality

## What Is Excluded

- live secrets
- local runtime data
- generated EPUBs
- SQLite databases
- personal Substack subscriptions
- personal playlist IDs
- local output folders

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp config/config.yaml.example config/config.yaml
cp config/.env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`.

## Configure Sources

Edit these generic files:

- `config/sources/substacks.json`
- `config/sources/youtube_playlists.json`
- `config/personality/hackernews.md`

Or run the Ratatui source manager:

```bash
cargo run --manifest-path tools/content-manager-tui/Cargo.toml
```

TUI features:

- Add/edit/delete/toggle Substack RSS sources.
- Add/edit/delete/toggle YouTube playlist IDs or playlist URLs.
- Open the `HN Personality` tab.
- Press `c` to copy an onboarding prompt for your LLM.
- Paste that prompt into your LLM.
- Copy the LLM response.
- Return to the TUI and press `p` to import/save the response into `config/personality/hackernews.md`.

The accepted HN personality format is Markdown containing one fenced JSON block:

````markdown
# Hacker News Personality Profile

```content-hub-personality
{
  "schema_version": 1,
  "briefing_context": "One paragraph describing preferred topics, depth, tone, and exclusion rules.",
  "positive_keywords": {
    "keyword or phrase": 2.0
  },
  "negative_keywords": {
    "keyword or phrase": -2.0
  }
}
```
````

HN ranking uses title-only keyword scoring plus HN score. `positive_keywords` and `negative_keywords` customize the ranking without sending full article bodies into ranking.

## Run Pipeline

```bash
PYTHONPATH=src .venv/bin/python -m content_hub_pack.cli   --project-root .   --config config/config.yaml   run-pipeline
```

Run a single stage:

```bash
PYTHONPATH=src .venv/bin/python -m content_hub_pack.cli --project-root . --config config/config.yaml ingest-substack --run-date 2026-05-05
PYTHONPATH=src .venv/bin/python -m content_hub_pack.cli --project-root . --config config/config.yaml ingest-youtube-playlist --run-date 2026-05-05
PYTHONPATH=src .venv/bin/python -m content_hub_pack.cli --project-root . --config config/config.yaml ingest-hn --run-date 2026-05-05
PYTHONPATH=src .venv/bin/python -m content_hub_pack.cli --project-root . --config config/config.yaml compose-daily-pack --run-date 2026-05-05
```

## Output

Expected files:

- `01-substack.epub`
- `02-video-notes.epub`
- `03-hn-brief.epub`

## Delivery

Default config uses `local_only`. To deliver to an Xteink/CrossPoint device, set:

```yaml
delivery:
  method: "crosspoint"
  host: "crosspoint.local"
  fallback_hosts: []
  remote_base_path: "/Daily"
  verify_copy: true
```

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```
