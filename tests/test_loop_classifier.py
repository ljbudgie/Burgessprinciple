"""Tests for the local-first institutional delay pattern classifier."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from iris.loop_classifier import classify_thread
from api import LoopClassificationRequest, classify_institutional_delay

_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _ROOT / "iris-local.py"
_SPEC = importlib.util.spec_from_file_location("iris_local_loop_test", _MODULE_PATH)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def message(date: str, sender: str, content_summary: str, direction: str, reference: str) -> dict:
    return {
        "date": date,
        "sender": sender,
        "content_summary": content_summary,
        "direction": direction,
        "reference": reference,
    }


@pytest.mark.parametrize(
    ("messages", "loop_type"),
    [
        (
            [
                message("2026-08-01", "Council", "Your answer is not sufficient; provide more information.", "institution", "A"),
                message("2026-08-03", "Individual", "I have supplied the requested information.", "individual", "B"),
                message("2026-08-06", "Council", "This is still not sufficient. Please provide further information.", "institution", "C"),
            ],
            "insufficiency",
        ),
        (
            [
                message("2026-08-01", "EHRC", "Please contact EASS.", "institution", "A"),
                message("2026-08-02", "EASS", "Please contact EHRC.", "institution", "B"),
                message("2026-08-03", "EHRC", "Please contact EASS.", "institution", "C"),
            ],
            "circular_referral",
        ),
        (
            [message("2026-08-01", "Council", "We cannot assess relief until the review is complete.", "institution", "A")],
            "precondition_stacking",
        ),
        (
            [
                message("2026-08-01", "Commission", "All points have been addressed.", "institution", "A"),
                message("2026-08-02", "Commission", "All points have been addressed.", "institution", "B"),
            ],
            "template_dismissal",
        ),
        (
            [message("2026-08-01", "Experian", "Verify your identity before we engage with this dispute.", "institution", "A")],
            "identity_loop",
        ),
        (
            [
                message("2026-08-01", "Individual", "My established adjustment is email-only; do not call.", "individual", "A"),
                message("2026-08-02", "Institution", "Please call us to discuss this.", "institution", "B"),
            ],
            "channel_redirect",
        ),
    ],
)
def test_classifies_each_delay_pattern(messages, loop_type):
    finding = classify_thread(messages, "Example Institution")

    assert finding.loop_detected is True
    assert finding.loop_type == loop_type
    assert finding.loop_count >= 1
    assert finding.accountability_finding == "NULL"
    assert finding.as_dict()["requires_human_confirmation"] is True


def test_finding_is_sovereign_when_a_named_individual_is_identified():
    finding = classify_thread(
        [
            message("2026-08-01", "Jane Smith", "Verify your identity before we engage.", "institution", "A"),
        ],
        "Example Institution",
    )

    assert finding.named_individual == "Jane Smith"
    assert finding.accountability_finding == "SOVEREIGN"


def test_no_loop_has_zero_elapsed_days_and_null_type():
    finding = classify_thread(
        [message("2026-08-01", "Institution", "We have received your message.", "institution", "A")],
        "Example Institution",
    )

    assert finding.loop_detected is False
    assert finding.loop_type is None
    assert finding.loop_count == 0
    assert finding.days_consumed == 0
    assert finding.summary.startswith("NO LOOP")


def test_rejects_invalid_message_shape():
    with pytest.raises(ValueError, match="non-empty date"):
        classify_thread([{"date": "", "sender": "Institution", "content_summary": "text"}])


def test_finding_schema_is_valid_json_and_covers_endpoint_fields():
    schema = json.loads((_ROOT / "schemas" / "loop-finding.v1.json").read_text())
    finding = classify_thread(
        [message("2026-08-01", "Institution", "Verify your identity before we engage.", "institution", "A")]
    ).as_dict()

    assert set(schema["required"]).issubset(finding)
    assert schema["properties"]["loop_type"]["enum"][-1] is None


def test_api_endpoint_function_returns_provisional_finding():
    result = classify_institutional_delay(
        LoopClassificationRequest(
            institution="Example Institution",
            messages=[
                message("2026-08-01", "Institution", "Verify your identity before we engage.", "institution", "A")
            ],
        )
    )

    assert result.loop_type == "identity_loop"
    assert result.requires_human_confirmation is True


class TestLoopEndpoint:
    @pytest.fixture(autouse=True)
    def _setup_client(self):
        try:
            from starlette.testclient import TestClient

            self.client = TestClient(_MOD.create_app("test prompt"))
        except ImportError:
            self.client = None

    def test_returns_provisional_loop_finding(self):
        if self.client is None:
            pytest.skip("starlette.testclient not available")
        response = self.client.post(
            "/loop/classify",
            json={
                "institution": "Example Institution",
                "messages": [
                    message("2026-08-01", "Institution", "Verify your identity before we engage.", "institution", "A")
                ],
            },
        )

        assert response.status_code == 200
        assert response.json()["loop_type"] == "identity_loop"
        assert response.json()["provisional"] is True

    def test_rejects_invalid_request(self):
        if self.client is None:
            pytest.skip("starlette.testclient not available")
        response = self.client.post("/loop/classify", json={"messages": []})

        assert response.status_code == 400
        assert response.json()["error"] == "messages must be a non-empty array."
