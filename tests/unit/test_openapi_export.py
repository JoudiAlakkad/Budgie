"""The OpenAPI spec is the contract: deployment config must not change it."""

from pathlib import Path

import pytest

from app.openapi_export import main, render_spec


def test_spec_does_not_depend_on_deployment_config(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = render_spec()

    monkeypatch.setenv("LLM_BASE_URL", "http://other-server:8080/v1")
    monkeypatch.setenv("LLM_MODEL", "llava:7b")
    monkeypatch.setenv("MAX_UPLOAD_MB", "3")

    assert render_spec() == baseline


def test_check_detects_drift(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"

    assert main(["--out", str(out), "--check"]) == 1
    assert main(["--out", str(out)]) == 0
    assert main(["--out", str(out), "--check"]) == 0

    out.write_text("{}\n", encoding="utf-8")
    assert main(["--out", str(out), "--check"]) == 1
