from pathlib import Path

import requests

from content_hub_pack.models import AIConfig, DeliveryConfig, HackerNewsConfig, OutputConfig, RuntimeConfig, Settings, YoutubeConfig
from content_hub_pack.workflows import deliver_to_xteink_stage


class _DummyResponse:
    def __init__(self, text: str = "OK", json_payload=None, status_code: int = 200) -> None:
        self.text = text
        self._json_payload = json_payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json_payload


def test_deliver_to_xteink_retries_crosspoint_until_available(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(
        project_root=tmp_path,
        timezone="Europe/Paris",
        schedule="30 6 * * *",
        substack_publications=[],
        youtube=YoutubeConfig(playlists=[]),
        hackernews=HackerNewsConfig(),
        delivery=DeliveryConfig(
            method="crosspoint",
            host="crosspoint.local",
            fallback_hosts=["10.0.0.2"],
            remote_base_path="/Daily",
            retry_interval_seconds=7,
            retry_timeout_minutes=60,
        ),
        output=OutputConfig(root_dir=tmp_path, folder_prefix="output", folder_date_format="%d-%m-%Y"),
        runtime=RuntimeConfig(db_path=tmp_path / "data/runtime/test.sqlite3", staging_dir=tmp_path / "data/staging"),
        ai=AIConfig(provider="openrouter", model="nvidia/nemotron-3-super-120b-a12b:free", base_url="https://openrouter.ai/api/v1"),
    )

    artifact_dir = tmp_path / "output_23-04-2026"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "01-substack.epub"
    artifact.write_bytes(b"epub-bytes")

    ping_calls: list[str] = []
    list_calls: list[tuple[str, str]] = []
    mkdir_calls: list[tuple[str, str, str]] = []
    sleep_calls: list[int] = []
    upload_calls: list[tuple[str, str]] = []
    daily_exists = False

    def fake_get(url: str, timeout: int, params=None):
        nonlocal daily_exists
        if url.endswith("/api/status"):
            ping_calls.append(url)
            if len(ping_calls) <= 2:
                raise requests.ConnectionError("device offline")
            return _DummyResponse('{"mode":"wifi"}', json_payload={"mode": "wifi"})
        if url.endswith("/api/files"):
            requested_path = (params or {}).get("path", "/")
            list_calls.append((url, requested_path))
            if requested_path == "/":
                return _DummyResponse(json_payload=[{"name": "Daily", "isDirectory": True, "size": 0}], text="[]")
            if requested_path == "/Daily":
                payload = [{"name": "23-04-2026", "isDirectory": True, "size": 0}] if daily_exists else []
                return _DummyResponse(json_payload=payload, text="[]")
            if requested_path == "/Daily/23-04-2026":
                return _DummyResponse(json_payload=[], text="[]")
            return _DummyResponse(json_payload=[], text="[]")
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url: str, timeout: int, params=None, files=None, data=None):
        nonlocal daily_exists
        if url.endswith("/mkdir"):
            mkdir_calls.append((url, data["path"], data["name"]))
            assert data["path"] == "/Daily"
            assert data["name"] == "23-04-2026"
            daily_exists = True
            return _DummyResponse("created")
        if url.endswith("/upload"):
            upload_calls.append((url, params["path"]))
            file_tuple = files["file"]
            assert file_tuple[0] == "01-substack.epub"
            return _DummyResponse("uploaded")
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr("content_hub_pack.workflows.requests.get", fake_get)
    monkeypatch.setattr("content_hub_pack.workflows.requests.post", fake_post)
    monkeypatch.setattr("content_hub_pack.workflows.time.sleep", lambda seconds: sleep_calls.append(seconds))

    result = deliver_to_xteink_stage(settings, "2026-04-23")

    assert result.status == "success"
    assert result.details["target_dir"] == "/Daily/23-04-2026"
    assert result.details["attempts"] == 2
    assert result.details["delivered_files"] == ["http://crosspoint.local/Daily/23-04-2026/01-substack.epub"]
    assert sleep_calls == [7]
    assert mkdir_calls == [("http://crosspoint.local/mkdir", "/Daily", "23-04-2026")]
    assert upload_calls == [("http://crosspoint.local/upload", "/Daily/23-04-2026")]
