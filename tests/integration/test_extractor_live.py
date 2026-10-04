"""Live checks against a real model server (`LLM_*` settings). Never run in CI.

pytest -c backend/pyproject.toml --rootdir . -m integration tests/integration
LIVE_RECEIPT_IMAGE=/path/to/receipt.jpg pytest ... -m integration tests/integration
"""

import mimetypes
import os
from pathlib import Path

import pytest

from app.ai.client import LLMClient
from app.ai.extractor import ExtractionResult, Extractor
from app.ai.prompts import load_prompts
from app.config import Settings
from app.errors import UnreadableImage

pytestmark = pytest.mark.integration

BROKEN_JPEG = b"\xff\xd8\xff" + b"this is not image data " * 8


@pytest.fixture(scope="module")
def extractor() -> Extractor:
    settings = Settings()
    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
    if not client.ping():
        pytest.skip(f"no model server at {settings.llm_base_url}")
    return Extractor(
        client,
        load_prompts(settings.prompt_version),
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )


def test_broken_jpeg_is_unreadable(extractor: Extractor) -> None:
    with pytest.raises(UnreadableImage):
        extractor.extract(BROKEN_JPEG, "image/jpeg")


def test_real_receipt_is_extracted(extractor: Extractor) -> None:
    path = os.environ.get("LIVE_RECEIPT_IMAGE")
    if not path:
        pytest.skip("LIVE_RECEIPT_IMAGE is not set")
    image = Path(path)
    mime = mimetypes.guess_type(image.name)[0] or "image/jpeg"

    result = extractor.extract(image.read_bytes(), mime)

    assert isinstance(result, ExtractionResult)
    assert result.model == extractor.model
    assert result.latency_s > 0
