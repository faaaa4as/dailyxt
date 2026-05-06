from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from content_hub_pack.models import HackerNewsItem


POSITIVE_WEIGHTS = {
    "agent": 3.0,
    "agents": 3.0,
    "agentic": 2.5,
    "ai": 2.5,
    "llm": 2.5,
    "model": 1.5,
    "models": 1.5,
    "language": 1.8,
    "language model": 2.2,
    "inference": 2.0,
    "gpu": 1.8,
    "tpu": 1.8,
    "cloud": 1.2,
    "developer": 1.8,
    "infra": 1.8,
    "infrastructure": 1.8,
    "crypto": 2.0,
    "exchange": 2.0,
    "market": 2.0,
    "markets": 2.0,
    "trading": 2.0,
    "quant": 2.0,
    "protocol": 1.5,
    "coding": 2.2,
    "code": 1.5,
    "programming": 1.5,
    "compiler": 1.5,
    "git": 1.5,
    "github": 1.2,
    "copilot": 2.0,
    "billing": 1.2,
    "pricing": 1.2,
    "postgres": 2.5,
    "postgresql": 2.5,
    "database": 2.0,
    "sql": 1.4,
    "backup": 1.4,
    "storage": 1.0,
    "memory": 2.0,
    "ram": 2.2,
    "cache": 1.5,
    "rust": 2.2,
    "wasm": 2.2,
    "webassembly": 2.2,
    "vintage": 2.4,
    "retro": 2.0,
    "history": 1.4,
    "historical": 1.4,
    "extension": 1.0,
    "extensions": 1.0,
    "security": 1.6,
    "exploit": 2.4,
    "exploits": 2.4,
    "shell": 1.4,
    "sudo": 1.0,
    "sandbox": 1.3,
    "kernel": 1.2,
    "linux": 1.0,
    "open source": 1.2,
    "self-host": 1.0,
    "mcp": 1.5,
    "performance": 1.2,
    "research": 1.2,
    "attention": 2.2,
    "focus": 1.8,
    "meditation": 1.8,
    "cognition": 2.0,
    "brain": 1.0,
    "mind": 0.8,
    "thinking": 0.8,
    "productivity": 1.0,
    "disattention": 1.6,
    "mind wander": 1.4,
    "brainfog": 1.0,
    "brain fog": 1.0,
    "caffeine": 0.8,
    "tool": 1.0,
    "tools": 1.0,
    "benchmark": 1.0,
    "benchmarks": 1.0,
    "rf": 0.8,
    "pcb": 1.0,
    "devboard": 1.0,
    "devboards": 1.0,
    "firmware": 1.0,
    "gtfobins": 2.5,
}

NEGATIVE_WEIGHTS = {
    "politics": -2.0,
    "funding": -1.5,
    "gadget": -1.5,
    "celebrity": -2.5,
    "drama": -3.0,
    "smartphone": -0.3,
    "tractor": -6.0,
    "tractors": -6.0,
    "agriculture": -4.0,
    "agricultural": -4.0,
    "farming": -4.0,
    "farm": -2.0,
    "car": -1.5,
    "cars": -1.5,
    "automotive": -2.0,
}

PERSONALITY_FENCE_RE = re.compile(r"```(?:content-hub-personality|json)\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass(slots=True)
class HackerNewsPersonality:
    positive_keywords: dict[str, float] = field(default_factory=dict)
    negative_keywords: dict[str, float] = field(default_factory=dict)
    briefing_context: str = ""


DEFAULT_PERSONALITY = HackerNewsPersonality(
    positive_keywords=POSITIVE_WEIGHTS.copy(),
    negative_keywords=NEGATIVE_WEIGHTS.copy(),
    briefing_context="",
)


def _number_map(raw: object, field_name: str) -> dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be an object mapping keyword to numeric weight")
    result: dict[str, float] = {}
    for keyword, weight in raw.items():
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError(f"{field_name} contains an empty/non-string keyword")
        if not isinstance(weight, int | float):
            raise ValueError(f"weight for {keyword!r} must be numeric")
        result[keyword.strip().lower()] = float(weight)
    return result


def parse_hn_personality_markdown(markdown: str) -> HackerNewsPersonality:
    match = PERSONALITY_FENCE_RE.search(markdown)
    if not match:
        raise ValueError("personality markdown must contain a fenced content-hub-personality JSON object")
    payload = json.loads(match.group(1))
    if payload.get("schema_version") != 1:
        raise ValueError("personality schema_version must be 1")
    positives = POSITIVE_WEIGHTS.copy()
    positives.update(_number_map(payload.get("positive_keywords"), "positive_keywords"))
    negatives = NEGATIVE_WEIGHTS.copy()
    negatives.update(_number_map(payload.get("negative_keywords"), "negative_keywords"))
    briefing_context = str(payload.get("briefing_context", "")).strip()
    return HackerNewsPersonality(
        positive_keywords=positives,
        negative_keywords=negatives,
        briefing_context=briefing_context,
    )


def load_hn_personality(path: Path | None) -> HackerNewsPersonality:
    if path is None or not path.exists():
        return DEFAULT_PERSONALITY
    return parse_hn_personality_markdown(path.read_text())


def _keyword_score(text: str, positive_keywords: dict[str, float], negative_keywords: dict[str, float], multiplier: float = 1.0) -> float:
    score = 0.0
    haystack = text.lower()
    for keyword, weight in positive_keywords.items():
        if keyword in haystack:
            score += weight * multiplier
    for keyword, weight in negative_keywords.items():
        if keyword in haystack:
            score += weight * multiplier
    return score


def relevance_score(item: HackerNewsItem, personality: HackerNewsPersonality | None = None) -> float:
    profile = personality or DEFAULT_PERSONALITY
    # Popularity should matter, but it should not overwhelm topical fit.
    # Keep topical scoring title-only so ranking stays cheap and does not favor long fetched bodies.
    score = math.log1p(max(float(item.score), 1.0)) * 1.25
    score += _keyword_score(item.title, profile.positive_keywords, profile.negative_keywords, 1.0)
    return round(score, 3)


def rank_items(
    items: list[HackerNewsItem],
    final_count: int,
    min_relevance_score: float = 15.0,
    min_selected_items: int = 3,
    personality: HackerNewsPersonality | None = None,
) -> list[HackerNewsItem]:
    scored: list[HackerNewsItem] = []
    seen_urls: set[str] = set()
    for item in items:
        if item.url in seen_urls:
            continue
        item.relevance_score = relevance_score(item, personality)
        seen_urls.add(item.url)
        scored.append(item)
    scored.sort(key=lambda item: item.relevance_score, reverse=True)
    filtered = [item for item in scored if item.relevance_score >= min_relevance_score]
    if len(filtered) < min_selected_items:
        return scored[: min(final_count, max(min_selected_items, 0))]
    return filtered[:final_count]
