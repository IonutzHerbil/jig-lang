"""Model backends for the bench: any Gemini model (jig.llm), or 'reference', which replays the task's reference."""

from __future__ import annotations

from pathlib import Path

from jig.llm import Gemini, Reply


class Model:
    def __init__(self, name: str, temperature: float, rpm: int) -> None:
        self.llm = Gemini(name, temperature, rpm)

    def __call__(self, system: str, turns: list[str], task_dir: Path, lang: str) -> Reply:
        return self.llm(system, turns)


class Reference:
    """Replays reference.<ext>: checks the harness and the hidden tests without a model."""

    def __call__(self, system: str, turns: list[str], task_dir: Path, lang: str) -> Reply:
        ext = "jig" if lang == "jig" else "py"
        return Reply(f"```{lang}\n{(task_dir / f'reference.{ext}').read_text(encoding='utf-8')}```")


def make_model(name: str, temperature: float, rpm: int):
    return Reference() if name == "reference" else Model(name, temperature, rpm)
