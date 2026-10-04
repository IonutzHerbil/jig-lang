"""Model backends: Gemini over REST, and 'reference', which replays the task's reference solution."""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class Reply:
    text: str
    tokens: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0  # visible answer plus hidden thinking
    cached_tokens: int = 0  # prompt tokens served from Gemini's implicit cache (billed at a discount)


def _saved_key() -> str | None:
    """On Windows, a key saved with setx after this terminal started is only in the registry."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as env:
            return winreg.QueryValueEx(env, "GEMINI_API_KEY")[0]
    except (ImportError, OSError):
        return None


class Gemini:
    def __init__(self, model: str, temperature: float, rpm: int) -> None:
        self.key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or _saved_key()
        if not self.key:
            raise SystemExit("set GEMINI_API_KEY (https://aistudio.google.com/apikey)")
        self.model, self.temperature = model, temperature
        self.gap = 60.0 / rpm
        self.last = 0.0

    def __call__(self, system: str, turns: list[str], task_dir: Path, lang: str) -> Reply:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user" if i % 2 == 0 else "model", "parts": [{"text": t}]} for i, t in enumerate(turns)],
            "generationConfig": {"temperature": self.temperature},
        }
        for attempt in range(6):
            time.sleep(max(0.0, self.last + self.gap - time.monotonic()))
            self.last = time.monotonic()
            req = urllib.request.Request(
                GEMINI_URL.format(model=self.model),
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "x-goog-api-key": self.key},
            )
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    data = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(10 * 2**attempt)
                    continue
                raise SystemExit(f"gemini {e.code}: {e.read().decode(errors='replace')[:500]}")
            except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
                # Dropped connections and timeouts are transient: wait and retry.
                if attempt < 5:
                    time.sleep(10 * 2**attempt)
                    continue
                raise SystemExit(f"gemini: network error after retries: {e}")
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            use = data.get("usageMetadata", {})
            return Reply(
                text,
                tokens=use.get("totalTokenCount", 0),
                prompt_tokens=use.get("promptTokenCount", 0),
                output_tokens=use.get("candidatesTokenCount", 0) + use.get("thoughtsTokenCount", 0),
                cached_tokens=use.get("cachedContentTokenCount", 0),
            )
        raise SystemExit("gemini: retries exhausted")


class Reference:
    """Replays reference.<ext>: checks the harness and the hidden tests without a model."""

    def __call__(self, system: str, turns: list[str], task_dir: Path, lang: str) -> Reply:
        ext = "jig" if lang == "jig" else "py"
        return Reply(f"```{lang}\n{(task_dir / f'reference.{ext}').read_text(encoding='utf-8')}```")


def make_model(name: str, temperature: float, rpm: int):
    return Reference() if name == "reference" else Gemini(name, temperature, rpm)
