# Hacker News Personality Profile

This file is managed by `tools/content-manager-tui` and read by the HN ranker.
Only the fenced JSON block below is parsed. Keep `schema_version` at `1`.

```content-hub-personality
{
  "schema_version": 1,
  "briefing_context": "Describe the reader's durable interests, preferred depth, and what makes an HN item worth attention.",
  "positive_keywords": {
    "llm": 2.5,
    "developer tools": 2.0,
    "database": 2.0,
    "security": 1.6
  },
  "negative_keywords": {
    "celebrity": -2.5,
    "drama": -3.0
  }
}
```
