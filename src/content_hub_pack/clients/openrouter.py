from __future__ import annotations

import os

import requests

from content_hub_pack.models import AIConfig


def generate_text(ai: AIConfig, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for AI generation")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        f"{ai.base_url.rstrip('/')}/chat/completions",
        headers=headers,
        json={
            "model": ai.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = error.get("code", "unknown")
        message = error.get("message", "unknown error")
        raise RuntimeError(f"OpenRouter error {code}: {message}")
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected OpenRouter response shape: {payload}") from exc
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
    return str(content).strip()
