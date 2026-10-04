"""Versioned prompts: `ai/prompts/<PROMPT_VERSION>/{system,user,repair}.txt`.

The files ship as package data (`backend/pyproject.toml`), so they are read through
`importlib.resources` and work in a non-editable install.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

PROMPT_FILES = ("system", "user", "repair")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")


class UnknownPromptVersion(ValueError):
    """`PROMPT_VERSION` names no prompt folder."""


@dataclass(frozen=True)
class Prompts:
    version: str
    system: str
    user: str
    repair: str  # contains `{errors}`, filled with str.replace


def available_versions() -> list[str]:
    """The prompt folders that hold every prompt file."""
    root = files(__name__)
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and all(entry.joinpath(f"{n}.txt").is_file() for n in PROMPT_FILES)
    )


@lru_cache
def load_prompts(version: str) -> Prompts:
    """Load one prompt version; raises `UnknownPromptVersion` for a missing or bad name."""
    versions = available_versions()
    if not _VERSION_PATTERN.fullmatch(version) or ".." in version or version not in versions:
        raise UnknownPromptVersion(
            f"Unknown prompt version {version!r}; available: {', '.join(versions) or 'none'}"
        )
    folder = files(__name__).joinpath(version)
    texts = {
        name: folder.joinpath(f"{name}.txt").read_text(encoding="utf-8").strip()
        for name in PROMPT_FILES
    }
    return Prompts(version=version, **texts)
