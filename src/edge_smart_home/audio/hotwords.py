from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import tempfile


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HotwordValidationResult:
    valid: tuple[str, ...]
    invalid: tuple[str, ...]


def validate_cjkchar_hotwords(tokens_path: Path, hotwords_path: Path) -> HotwordValidationResult:
    tokens = _load_tokens(tokens_path)
    valid: list[str] = []
    invalid: list[str] = []
    for raw_line in hotwords_path.read_text(encoding="utf-8").splitlines():
        phrase = raw_line.split(":", 1)[0].strip()
        if not phrase or phrase.startswith("#"):
            continue
        units = [character for character in phrase if not character.isspace()]
        (valid if all(unit in tokens for unit in units) else invalid).append(raw_line.strip())
    return HotwordValidationResult(tuple(valid), tuple(invalid))


def create_validated_hotwords_file(
    tokens_path: Path, hotwords_path: Path
) -> tuple[Path | None, HotwordValidationResult]:
    result = validate_cjkchar_hotwords(tokens_path, hotwords_path)
    for phrase in result.invalid:
        LOGGER.warning("skip hotword incompatible with model tokens: %s", phrase)
    if not result.valid:
        return None, result
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="edge-smart-home-hotwords-", suffix=".txt", delete=False
    )
    with handle:
        handle.write("\n".join(result.valid) + "\n")
    return Path(handle.name), result


def _load_tokens(path: Path) -> set[str]:
    tokens = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        token, _, _ = line.rpartition(" ")
        tokens.add(token or line.strip())
    return tokens
