from content_hub_pack.models import HackerNewsItem
from content_hub_pack.ranking import rank_items, relevance_score


def test_rank_items_prefers_ai_and_market_topics() -> None:
    items = [
        HackerNewsItem(
            hn_id=1,
            title="New AI agents for developer infrastructure and crypto markets",
            url="https://example.com/ai-markets",
            author="alice",
            score=120,
            created_at="2026-04-23T06:00:00+00:00",
            text="A strong essay about agents, markets, and research tools.",
            top_comments=[],
        ),
        HackerNewsItem(
            hn_id=2,
            title="Celebrity smartphone drama recap",
            url="https://example.com/drama",
            author="bob",
            score=180,
            created_at="2026-04-23T06:00:00+00:00",
            text="Low-signal drama and gadget talk.",
            top_comments=[],
        ),
    ]

    ranked = rank_items(items, final_count=2)

    assert ranked[0].hn_id == 1


def test_relevance_score_penalizes_tractor_story() -> None:
    tractor = HackerNewsItem(
        hn_id=10,
        title="Alberta startup sells no-tech tractors for half price",
        url="https://example.com/tractors",
        author="alice",
        score=1200,
        created_at="2026-04-23T06:00:00+00:00",
        text="A story about tractors, farming, and agricultural machinery.",
        top_comments=["farmers like simple tractors"],
    )
    llm = HackerNewsItem(
        hn_id=11,
        title="New open source coding agent for LLM infrastructure",
        url="https://example.com/agent",
        author="bob",
        score=400,
        created_at="2026-04-23T06:00:00+00:00",
        text="A technical post about agents, coding, cloud inference, and developer tooling.",
        top_comments=["useful for self-hosted AI workflows"],
    )

    assert relevance_score(llm) > relevance_score(tractor)


def test_rank_items_filters_weak_tail() -> None:
    strong = HackerNewsItem(
        hn_id=20,
        title="Parallel agents for coding workflows",
        url="https://example.com/parallel-agents",
        author="alice",
        score=150,
        created_at="2026-04-23T06:00:00+00:00",
        text="Agentic coding infrastructure and developer tooling.",
        top_comments=["good agent coordination and worktree discussion"],
    )
    weak = HackerNewsItem(
        hn_id=21,
        title="Another Day Has Come",
        url="https://example.com/apple-opinion",
        author="bob",
        score=180,
        created_at="2026-04-23T06:00:00+00:00",
        text="A broad opinion piece about Apple leadership.",
        top_comments=["mostly general platform commentary"],
    )

    ranked = rank_items([strong, weak], final_count=10, min_relevance_score=15.0, min_selected_items=1)

    assert [item.hn_id for item in ranked] == [20]


def test_relevance_score_boosts_security_and_database_infra() -> None:
    security = HackerNewsItem(
        hn_id=30,
        title="GTFOBins",
        url="https://gtfobins.org/",
        author="alice",
        score=140,
        created_at="2026-04-28T06:00:00+00:00",
        text="A collection of Unix binaries that can be used to bypass local security restrictions.",
        top_comments=["useful in security and restricted shell contexts with sudo"],
    )
    generic = HackerNewsItem(
        hn_id=31,
        title="Can You Find the Comet?",
        url="https://example.com/comet",
        author="bob",
        score=220,
        created_at="2026-04-28T06:00:00+00:00",
        text="A visual puzzle about spotting a comet.",
        top_comments=[],
    )

    assert relevance_score(security) > relevance_score(generic)


def test_relevance_score_uses_titles_only_not_article_text_or_comments() -> None:
    title_match = HackerNewsItem(
        hn_id=50,
        title="LLM inference agents for developer infrastructure",
        url="https://example.com/plain",
        author="alice",
        score=40,
        created_at="2026-05-04T06:00:00+00:00",
        text="",
        top_comments=[],
        article_text="",
        article_fetch_status="empty",
    )
    body_match = HackerNewsItem(
        hn_id=51,
        title="Quiet unrelated post",
        url="https://example.com/other",
        author="bob",
        score=40,
        created_at="2026-05-04T06:00:00+00:00",
        text="LLM inference developer infrastructure agents database memory.",
        top_comments=["LLM inference developer infrastructure agents database memory."],
        article_text="LLM inference developer infrastructure agents database memory.",
        article_fetch_status="ok",
    )

    assert relevance_score(title_match) > relevance_score(body_match)


def test_rank_items_falls_back_to_top_slice_when_threshold_filters_everything() -> None:
    first = HackerNewsItem(
        hn_id=40,
        title="Interesting systems post",
        url="https://example.com/systems",
        author="alice",
        score=50,
        created_at="2026-04-28T06:00:00+00:00",
        text="Mostly generic text without exact scorer keywords.",
        top_comments=[],
    )
    second = HackerNewsItem(
        hn_id=41,
        title="Another useful engineering post",
        url="https://example.com/engineering",
        author="bob",
        score=45,
        created_at="2026-04-28T06:00:00+00:00",
        text="Also generic enough to stay under a harsh threshold.",
        top_comments=[],
    )
    third = HackerNewsItem(
        hn_id=42,
        title="Mildly relevant devtools article",
        url="https://example.com/devtools",
        author="carol",
        score=40,
        created_at="2026-04-28T06:00:00+00:00",
        text="Developer workflow notes.",
        top_comments=[],
    )

    ranked = rank_items([first, second, third], final_count=10, min_relevance_score=30.0, min_selected_items=3)

    assert len(ranked) == 3
