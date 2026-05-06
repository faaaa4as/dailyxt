from __future__ import annotations

import shutil

from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

from content_hub_pack.models import YoutubeVideo


def _js_runtimes() -> dict[str, dict[str, str]]:
    runtimes: dict[str, dict[str, str]] = {}
    for runtime in ("node", "bun", "deno"):
        path = shutil.which(runtime)
        if path:
            runtimes[runtime] = {"path": path}
    return runtimes


def fetch_playlist_videos(playlist_id: str, max_items: int) -> list[YoutubeVideo]:
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    ydl_opts = {
        "quiet": True,
        "extract_flat": False,
        "skip_download": True,
        "remote_components": ["ejs:github"],
    }
    js_runtimes = _js_runtimes()
    if js_runtimes:
        ydl_opts["js_runtimes"] = js_runtimes

    transcript_api = YouTubeTranscriptApi()

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    videos: list[YoutubeVideo] = []
    entries = info.get("entries", [])[:max_items]
    for entry in entries:
        video_id = entry.get("id")
        transcript_status = "missing"
        transcript_text = ""
        if video_id:
            try:
                transcript = transcript_api.fetch(video_id, languages=["en"])
                transcript_text = "\n".join(chunk.text for chunk in transcript)
                transcript_status = "available"
            except (NoTranscriptFound, TranscriptsDisabled):
                transcript_status = "missing"
        videos.append(
            YoutubeVideo(
                video_id=video_id or "",
                title=entry.get("title", "Untitled"),
                channel=entry.get("channel", entry.get("uploader", "")),
                published_at=entry.get("upload_date", ""),
                duration_seconds=int(entry.get("duration") or 0),
                url=entry.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}"),
                transcript_status=transcript_status,
                transcript_text=transcript_text,
                description=entry.get("description", ""),
            )
        )
    return videos
