"""Opt in explicitly; normal test runs must never consume paid-model tokens."""

import os
from pathlib import Path

import pytest

from agent_infertriage.cli import pipeline


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("INFERTRIAGE_RUN_LIVE") != "1", reason="Live model opt-in absent"
)
def test_explicit_live_model_smoke(tmp_path):
    config_path = os.environ.get("INFERTRIAGE_MODEL_CONFIG")
    assert config_path, "Set INFERTRIAGE_MODEL_CONFIG to an explicitly authorized model config"
    log = tmp_path / "log.txt"
    log.write_text("Agent started. A model request took 2 seconds. No verified server timings.\n")
    report, code = pipeline(
        log, tmp_path / "live", mode="agent", format_name="raw-text", model_config=Path(config_path)
    )
    assert code == 0, report["error"]
    assert report["diagnosis"]["model_call_count"] >= 1
    assert report["diagnosis"]["usage_completeness"] == "complete"
