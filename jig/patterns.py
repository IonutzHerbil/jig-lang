"""Pattern Catalog: canonical implementations for common tasks.

Patterns define "the one way" to do common things. When the checker detects
an anti-pattern or a common task, it suggests the canonical pattern.

Example pattern:

    pattern http-endpoint

    problem: Handling HTTP POST requests with validation and error responses
    solution:
        def handle_request(ctx: Ctx, request: Request) -> Result[Response, HttpError]:
            effects: log, net
            # 1. Validate input
            validated = validate_request(request)?
            # 2. Execute business logic
            result = execute_logic(ctx, validated)?
            # 3. Log success
            ctx.log.info(f"Request processed: {result.id}")
            # 4. Return response
            return Ok(Response(status=200, body=result))
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .diagnostics import Diagnostic


@dataclass
class Pattern:
    """A canonical implementation pattern."""
    id: str
    problem: str  # What problem this solves
    solution: str  # The canonical code
    anti_patterns: list[str]  # Common mistakes
    file: str


def parse_pattern(path: Path) -> tuple[Pattern | None, list[Diagnostic]]:
    """Parse a .pattern file."""
    diags: list[Diagnostic] = []
    text = path.read_text(encoding="utf-8")
    lines = text.strip().split('\n')

    if not lines or not lines[0].startswith('pattern '):
        diags.append(Diagnostic(
            severity="error",
            code="P001",
            message="pattern file must start with 'pattern <id>'",
            file=str(path),
            line=1,
            col=1,
        ))
        return None, diags

    pattern_id = lines[0][8:].strip()

    # Parse fields
    fields = {
        'problem': [],
        'solution': [],
        'anti_patterns': [],
    }

    current_field = None

    for line in lines[1:]:
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith('problem:'):
            current_field = 'problem'
            fields['problem'].append(stripped[8:].strip())
        elif stripped.startswith('solution:'):
            current_field = 'solution'
        elif stripped.startswith('anti-pattern:'):
            current_field = 'anti_patterns'
            fields['anti_patterns'].append(stripped[13:].strip())
        elif current_field:
            fields[current_field].append(stripped)

    problem = ' '.join(fields['problem'])
    solution = '\n'.join(fields['solution'])
    anti_patterns = fields['anti_patterns']

    pattern = Pattern(
        id=pattern_id,
        problem=problem,
        solution=solution,
        anti_patterns=anti_patterns,
        file=str(path),
    )

    return pattern, diags


def load_patterns(project_root: Path) -> tuple[dict[str, Pattern], list[Diagnostic]]:
    """Load all patterns from .patterns/*.pattern files."""
    patterns: dict[str, Pattern] = {}
    all_diags: list[Diagnostic] = []

    patterns_dir = project_root / ".patterns"
    if not patterns_dir.exists():
        return patterns, all_diags

    for pattern_file in patterns_dir.glob("*.pattern"):
        pattern, diags = parse_pattern(pattern_file)
        all_diags.extend(diags)
        if pattern:
            patterns[pattern.id] = pattern

    return patterns, all_diags
