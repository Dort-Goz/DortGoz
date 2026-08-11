"""Gerçek pencere VLM'i için kare provenance ve evidence sözleşmesi."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from dortgoz.config import settings
from dortgoz.domain.taxonomy import CanonicalEventType
from dortgoz.events import EventEvidenceRef, WindowEvent
from dortgoz.pipeline import interpret
from dortgoz.pipeline.interpret import (
    VlmEvidenceContractError,
    _to_report,
    build_frame_references,
    interpret_window,
    report_schema,
    tier_schema,
)


def _event_raw(
    event_type: str,
    *,
    frame_id: str = "f_001",
    timestamp: float = 12.5,
) -> str:
    return json.dumps(
        {
            "summary": "Dikkat gerektiren bir hareket görülüyor.",
            "durum": "dikkat",
            "events": [
                {
                    "t": timestamp,
                    "desc": "Kişiler arasında fiziksel temas görülüyor.",
                    "evidence": [
                        {
                            "frame_id": frame_id,
                            "timestamp": timestamp,
                            "claim": "İki kişinin birbirini ittiği görülmektedir.",
                        }
                    ],
                    "severity_hint": "orta",
                    "event_type": event_type,
                }
            ],
            "uncertainties": [],
            "anomaly_type": event_type,
        },
        ensure_ascii=False,
    )


@pytest.fixture
def three_frames():
    return build_frame_references([4.0, 12.5, 19.0])


def test_supplied_frame_reference_is_accepted(three_frames):
    report = _to_report(
        0,
        30,
        _event_raw("physical_fight"),
        frame_refs=three_frames,
    )
    assert report.events[0].evidence[0].frame_id == "f_001"


def test_unknown_frame_reference_is_typed_failure(three_frames):
    with pytest.raises(VlmEvidenceContractError) as caught:
        _to_report(
            0,
            30,
            _event_raw("physical_fight", frame_id="f_999"),
            frame_refs=three_frames,
        )
    assert caught.value.code == "INVALID_VLM_EVIDENCE_REFERENCE"


def test_frame_timestamp_mismatch_is_typed_failure(three_frames):
    with pytest.raises(VlmEvidenceContractError) as caught:
        _to_report(
            0,
            30,
            _event_raw("physical_fight", timestamp=44.0),
            frame_refs=three_frames,
        )
    assert caught.value.code == "INVALID_VLM_EVIDENCE_TIMESTAMP"


def test_physical_fight_with_evidence_parses(three_frames):
    report = _to_report(
        0,
        30,
        _event_raw("physical_fight"),
        frame_refs=three_frames,
    )
    assert report.canonical_event_type == CanonicalEventType.PHYSICAL_FIGHT
    assert report.events[0].event_type == CanonicalEventType.PHYSICAL_FIGHT


@pytest.mark.parametrize(
    ("event_type", "legacy_type"),
    [
        (CanonicalEventType.POSSIBLE_THEFT, "hirsizlik"),
        (CanonicalEventType.POSSIBLE_ARMED_INCIDENT, "silahli_olay"),
    ],
)
def test_sensitive_canonical_types_parse(event_type, legacy_type, three_frames):
    report = _to_report(
        0,
        30,
        _event_raw(event_type.value),
        frame_refs=three_frames,
    )
    assert report.events[0].event_type == event_type
    assert report.anomaly_type == legacy_type


def test_normal_two_tier_branch_stays_evidence_free(three_frames):
    report = _to_report(
        0,
        30,
        '{"summary":"Sahne sakin.","durum":"olagan"}',
        frame_refs=three_frames,
    )
    assert report.events == []
    normal_branch = tier_schema()["oneOf"][0]
    assert list(normal_branch["properties"]) == ["summary", "durum"]
    assert "evidence" not in json.dumps(normal_branch)


def test_uncertain_can_carry_evidence_without_confirmation(three_frames):
    report = _to_report(
        0,
        30,
        _event_raw("uncertain"),
        frame_refs=three_frames,
    )
    assert report.events[0].event_type == CanonicalEventType.UNCERTAIN
    assert report.events[0].evidence
    assert not hasattr(report, "confirmed")


@pytest.mark.asyncio
async def test_thinking_escalation_uses_same_evidence_schema(monkeypatch):
    captured = {}

    async def fake_grab_frame(_video, _timestamp):
        return b"jpeg"

    async def fake_create_chat(_client, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=_event_raw(
                            "physical_fight",
                            frame_id="f_000",
                            timestamp=12.5,
                        )
                    ),
                    finish_reason="stop",
                    logprobs=None,
                )
            ]
        )

    monkeypatch.setattr(interpret, "grab_frame", fake_grab_frame)
    monkeypatch.setattr(interpret, "create_chat", fake_create_chat)
    monkeypatch.setattr(interpret, "main_client", lambda: object())
    monkeypatch.setattr(settings, "two_tier", True)

    evidence_frames = {}
    report = await interpret_window(
        Path("unused.mp4"),
        (0, 30),
        [12.5],
        think=True,
        captured_frames=evidence_frames,
    )

    assert report.events[0].evidence[0].frame_id == "f_000"
    assert evidence_frames["f_000"][0].timestamp == 12.5
    assert evidence_frames["f_000"][1] == b"jpeg"
    assert captured["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    schema = captured["response_format"]["json_schema"]["schema"]
    event_schema = schema["oneOf"][1]["properties"]["events"]["items"]
    assert event_schema["properties"]["evidence"]["minItems"] == 1
    # B-biçimi: model yalnız FRAME_ID görür; timestamp şemadan çıkarılır ve
    # parse sonrasında uygulama tarafından doldurulur.
    evidence_item = event_schema["properties"]["evidence"]["items"]
    assert "timestamp" not in evidence_item["properties"]
    assert "timestamp" not in evidence_item.get("required", [])
    content = captured["messages"][1]["content"]
    assert content[0]["text"] == "FRAME_ID: f_000"


def test_legacy_window_event_remains_valid():
    event = WindowEvent(t=2.0, desc="Eski fixture olayı", severity_hint="orta")
    assert event.evidence == []
    assert event.event_type is None


def test_frontend_ws_mirror_contains_evidence_contract():
    source = (
        Path(__file__).parents[2] / "frontend" / "src" / "types" / "events.ts"
    ).read_text(encoding="utf-8")
    assert "interface EventEvidenceRef" in source
    assert "evidence?: EventEvidenceRef[]" in source
    assert "event_type?: CanonicalEventType | null" in source


@pytest.mark.parametrize(
    "payload",
    [
        {"frame_id": "f_000", "timestamp": 1.0},
        {"frame_id": "bad", "timestamp": 1.0, "claim": "Gözlem var."},
        {"frame_id": "f_000", "timestamp": float("inf"), "claim": "Gözlem var."},
        {"frame_id": "f_000", "timestamp": 1.0, "claim": "x"},
    ],
)
def test_malformed_evidence_fails_pydantic(payload):
    with pytest.raises(ValidationError):
        EventEvidenceRef.model_validate(payload)


def test_model_schema_preserves_observation_first_order_and_hides_source_label():
    schema = report_schema()
    assert list(schema["properties"]) == [
        "summary",
        "events",
        "uncertainties",
        "anomaly_type",
    ]
    event_props = schema["properties"]["events"]["items"]["properties"]
    assert list(event_props) == [
        "t",
        "desc",
        "evidence",
        "severity_hint",
        "event_type",
    ]
    assert "source_label" not in json.dumps(schema)


def test_frame_ids_are_deterministic_unique_and_path_free():
    first = build_frame_references([1.25, 4.5, 9.0])
    second = build_frame_references([1.25, 4.5, 9.0])
    assert first == second
    assert [frame.frame_id for frame in first] == ["f_000", "f_001", "f_002"]
    assert len({frame.frame_id for frame in first}) == len(first)
    assert all("/" not in frame.frame_id and "\\" not in frame.frame_id for frame in first)
