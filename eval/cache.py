"""The local model-output cache (`data/eval/cache/`, git-ignored; decision 0028).

One file per configuration, case and run: `<config_id>/<case>-r<n>.json`. It holds the
extraction after the app's redaction (`redact_extraction`), never the raw model text,
plus the latency, the repair flag, the token count, the outcome and a fingerprint of
everything that went into the call. Infrastructure errors aren't cached, so a later run
retries them. Scoring rebuilds an `ExtractionResult` from an entry and runs the app's
`process_extraction` on it, so a scoring change never needs the model.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.ai.extractor import ExtractionResult, system_prompt
from app.ai.prompts import load_prompts
from app.ai.schema import OUTPUT_SPECS, ReceiptExtraction
from app.services.receipt_pipeline import redact_extraction

SCHEMA = 1
CACHED_OUTCOMES = frozenset({"extracted", "not_a_receipt", "malformed_output", "unreadable_image"})
_UNSAFE = re.compile(r"[^A-Za-z0-9.]+")


def config_id(model: str, prompt_version: str) -> str:
    """`gemma3:4b`, `v3` -> `gemma3_4b__v3`; safe as a directory and file name."""
    return f"{_UNSAFE.sub('_', model)}__{_UNSAFE.sub('_', prompt_version)}"


def fingerprint(
    *,
    model: str,
    prompt_version: str,
    temperature: float,
    max_tokens: int,
    image: bytes,
    run: int,
) -> str:
    """sha256 over the model, the prompt texts as sent, the response format, the
    parameters, the image bytes as sent and the run index."""
    prompts = load_prompts(prompt_version)
    spec = OUTPUT_SPECS[prompt_version]
    parts = {
        "model": model,
        "prompt_version": prompt_version,
        "system": system_prompt(prompts),
        "user": prompts.user,
        "repair": prompts.repair,
        "response_format": json.dumps(spec.response_format, sort_keys=True),
        "temperature": repr(float(temperature)),
        "max_tokens": max_tokens,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "run": run,
    }
    encoded = json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = SCHEMA
    fingerprint: str
    outcome: str  # extracted | not_a_receipt | malformed_output | unreadable_image
    error_code: str | None = None
    extraction: dict[str, Any] | None = None  # redacted ReceiptExtraction, JSON mode
    latency_s: float | None = None
    repaired: bool = False
    completion_tokens: int | None = None

    @classmethod
    def from_result(cls, result: ExtractionResult, fingerprint: str) -> "CacheEntry":
        return cls(
            fingerprint=fingerprint,
            outcome="extracted",
            extraction=redact_extraction(result.extraction).model_dump(mode="json"),
            latency_s=result.latency_s,
            repaired=result.repaired,
            completion_tokens=result.completion_tokens,
        )

    @classmethod
    def from_error(cls, code: str, latency_s: float | None, fingerprint: str) -> "CacheEntry":
        # Never the error's raw output: it is the model's unredacted text.
        return cls(fingerprint=fingerprint, outcome=code, error_code=code, latency_s=latency_s)

    def extraction_model(self) -> ReceiptExtraction | None:
        if self.extraction is None:
            return None
        return ReceiptExtraction.model_validate(self.extraction)

    def to_result(self, model: str, prompt_version: str) -> ExtractionResult:
        """The `ExtractionResult` that `process_extraction` takes; only for `extracted`."""
        extraction = self.extraction_model()
        if extraction is None:
            raise ValueError("only an extracted entry has a result")
        return ExtractionResult(
            extraction=extraction,
            model=model,
            prompt_version=prompt_version,
            latency_s=self.latency_s or 0.0,
            raw_output=extraction.model_dump_json(),
            repaired=self.repaired,
            completion_tokens=self.completion_tokens,
        )


def entry_path(cache_root: Path, config: str, case_id: str, run: int) -> Path:
    return cache_root / config / f"{case_id}-r{run}.json"


def is_cacheable(outcome: str) -> bool:
    return outcome in CACHED_OUTCOMES


def load(cache_root: Path, config: str, case_id: str, run: int) -> CacheEntry | None:
    """The entry, or None if there is none or it can't be read."""
    path = entry_path(cache_root, config, case_id, run)
    try:
        return CacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_atomic(path: Path, text: str) -> None:
    """Write through a temp file in the same directory and `os.replace`.

    The text is written as given (`newline=""`), so a report with CRLF line ends keeps them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def remove(cache_root: Path, config: str, case_id: str, run: int) -> None:
    """Delete an entry if there is one (`--force`: a failed new call leaves it missing)."""
    entry_path(cache_root, config, case_id, run).unlink(missing_ok=True)


def store(cache_root: Path, config: str, case_id: str, run: int, entry: CacheEntry) -> bool:
    """Write the entry; False (and nothing written) for an outcome that isn't cached."""
    if not is_cacheable(entry.outcome):
        return False
    write_atomic(
        entry_path(cache_root, config, case_id, run), entry.model_dump_json(indent=2) + "\n"
    )
    return True
