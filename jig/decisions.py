"""Decision Notebook: record and enforce architectural decisions.

Decisions are stored in .decisions/*.decision files. Each decision captures a
choice that was made and enforces consistency going forward.

Example decision:

    decision money-storage

    context: We need to store monetary amounts
    decision: Money is always stored in cents (int), never as floating point
    rationale: Floating point has precision errors. $0.10 + $0.20 != $0.30 in float.
    enforcement: Money newtype must use int base, never float
    examples:
        ✓ type Money = Money(int)
        ✗ type Money = Money(float)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic


@dataclass
class Decision:
    """An architectural decision recorded in the project."""
    id: str  # kebab-case identifier
    context: str  # Why this decision was needed
    decision: str  # What was decided
    rationale: str  # Why this was chosen
    enforcement: str  # How the compiler enforces it
    examples: list[tuple[bool, str]]  # (correct, code) pairs
    file: str
    line: int


@dataclass
class DecisionViolation:
    """A violation of a recorded decision."""
    decision_id: str
    message: str
    suggestion: str


def parse_decision(path: Path) -> tuple[Decision | None, list[Diagnostic]]:
    """Parse a .decision file.

    Format:
        decision <id>

        context: <text>
        decision: <text>
        rationale: <text>
        enforcement: <text>
        examples:
            ✓ <good code>
            ✗ <bad code>
    """
    diags: list[Diagnostic] = []
    text = path.read_text(encoding="utf-8")
    lines = text.strip().split('\n')

    if not lines or not lines[0].startswith('decision '):
        diags.append(Diagnostic(
            severity="error",
            code="D001",
            message="decision file must start with 'decision <id>'",
            file=str(path),
            line=1,
            col=1,
        ))
        return None, diags

    decision_id = lines[0][9:].strip()

    # Parse fields
    fields = {
        'context': [],
        'decision_text': [],
        'rationale': [],
        'enforcement': [],
    }
    examples: list[tuple[bool, str]] = []

    current_field = None

    for i, line in enumerate(lines[1:], start=2):
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith('context:'):
            current_field = 'context'
            fields['context'].append(stripped[8:].strip())
        elif stripped.startswith('decision:'):
            current_field = 'decision_text'
            fields['decision_text'].append(stripped[9:].strip())
        elif stripped.startswith('rationale:'):
            current_field = 'rationale'
            fields['rationale'].append(stripped[10:].strip())
        elif stripped.startswith('enforcement:'):
            current_field = 'enforcement'
            fields['enforcement'].append(stripped[12:].strip())
        elif stripped.startswith('examples:'):
            current_field = 'examples_block'
        elif stripped.startswith('✓') or stripped.startswith('✗'):
            is_correct = stripped[0] == '✓'
            code = stripped[1:].strip()
            examples.append((is_correct, code))
        elif current_field and current_field != 'examples_block':
            fields[current_field].append(stripped)

    # Join multi-line fields
    context = ' '.join(fields['context'])
    decision_text = ' '.join(fields['decision_text'])
    rationale = ' '.join(fields['rationale'])
    enforcement = ' '.join(fields['enforcement'])

    decision = Decision(
        id=decision_id,
        context=context,
        decision=decision_text,
        rationale=rationale,
        enforcement=enforcement,
        examples=examples,
        file=str(path),
        line=1,
    )

    return decision, diags


def load_decisions(project_root: Path) -> tuple[dict[str, Decision], list[Diagnostic]]:
    """Load all decisions from .decisions/*.decision files.

    Returns (decisions_by_id, diagnostics).
    """
    decisions: dict[str, Decision] = {}
    all_diags: list[Diagnostic] = []

    decisions_dir = project_root / ".decisions"
    if not decisions_dir.exists():
        return decisions, all_diags

    for decision_file in decisions_dir.glob("*.decision"):
        decision, diags = parse_decision(decision_file)
        all_diags.extend(diags)
        if decision:
            decisions[decision.id] = decision

    return decisions, all_diags


def check_decision(decision: Decision, code_element: dict[str, Any]) -> DecisionViolation | None:
    """Check if a code element violates a decision.

    This is a simple v0.1 implementation. In production, we'd have more sophisticated
    pattern matching and rule engines.

    Args:
        decision: The decision to check against
        code_element: Dict with 'type' ('newtype'|'function'|'import'), 'name', 'definition', etc.

    Returns:
        DecisionViolation if violated, None otherwise
    """
    # Simple pattern matching for v0.1
    # In production, we'd have a full rule language

    # Example: Money newtype must use int
    if decision.id == "money-storage":
        if code_element.get('type') == 'newtype' and code_element.get('name') == 'Money':
            if code_element.get('base') != 'int':
                return DecisionViolation(
                    decision_id=decision.id,
                    message=f"violates decision '{decision.id}': {decision.decision}. Rationale: {decision.rationale}",
                    suggestion=f"Use Money = Money(int) instead of Money = Money({code_element.get('base')})"
                )

    return None
