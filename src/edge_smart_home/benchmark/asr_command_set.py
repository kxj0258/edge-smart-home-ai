from __future__ import annotations

import json
from pathlib import Path


def load_commands(path: Path) -> list[str]:
    """Load non-empty, non-comment commands while preserving file order."""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def load_failed_commands(manifest_path: Path) -> list[str]:
    """Load unique failed expected texts from a recording manifest."""
    commands: list[str] = []
    seen: set[str] = set()
    with manifest_path.open(encoding="utf-8") as manifest:
        for line_number, line in enumerate(manifest, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid JSON at {manifest_path}:{line_number}: {error.msg}"
                ) from error
            expected = row.get("expected")
            if not isinstance(expected, str) or not expected.strip():
                raise ValueError(
                    f"missing expected command at {manifest_path}:{line_number}"
                )
            expected = expected.strip()
            if not bool(row.get("exact_match")) and expected not in seen:
                commands.append(expected)
                seen.add(expected)
    return commands
