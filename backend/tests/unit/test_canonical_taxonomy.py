"""Faz A canonical taxonomy ve compatibility adapter regresyonları."""

from __future__ import annotations

from pathlib import Path

import pytest

from dortgoz.domain.evidence import VerifiedEventType
from dortgoz.domain.taxonomy import (
    CANONICAL_UI_LABEL_TR,
    LEGACY_WS_TO_CANONICAL,
    PRODUCTION_SUPPORTED_EVENT_TYPES,
    CanonicalEventType,
    LegacyWsEventType,
    UnknownEventTypeError,
    canonical_event_type_from_domain,
    canonical_event_type_from_ws_label,
    is_production_supported,
    map_dataset_source_label,
)
from dortgoz.events import WindowReport


def test_every_legacy_ws_type_maps_to_a_production_type() -> None:
    assert set(LEGACY_WS_TO_CANONICAL) == set(LegacyWsEventType)
    assert set(LEGACY_WS_TO_CANONICAL.values()) <= PRODUCTION_SUPPORTED_EVENT_TYPES
    assert canonical_event_type_from_ws_label("hirsizlik") == CanonicalEventType.POSSIBLE_THEFT
    assert (
        canonical_event_type_from_ws_label("silahli_olay")
        == CanonicalEventType.POSSIBLE_ARMED_INCIDENT
    )


def test_legacy_wire_contract_exposes_canonical_type_without_changing_wire_value() -> None:
    report = WindowReport(window_start=0, window_end=30, anomaly_type="patlama", summary="x")

    assert report.anomaly_type == "patlama"
    assert report.canonical_event_type == CanonicalEventType.EXPLOSION
    assert CANONICAL_UI_LABEL_TR[report.canonical_event_type] == "patlama"


@pytest.mark.parametrize(
    ("source_label", "expected"),
    [
        ("Stealing", CanonicalEventType.POSSIBLE_THEFT),
        ("Shoplifting", CanonicalEventType.POSSIBLE_THEFT),
        ("Robbery", CanonicalEventType.POSSIBLE_THEFT),
        ("Burglary", CanonicalEventType.POSSIBLE_THEFT),
    ],
)
def test_dataset_source_labels_stay_distinct_while_mapping_to_theft_family(
    source_label: str, expected: CanonicalEventType
) -> None:
    mapping = map_dataset_source_label(source_label)

    assert mapping.source_label == source_label
    assert mapping.event_type == expected
    assert mapping.matched


def test_legacy_domain_values_remain_readable_but_are_not_production_supported() -> None:
    assert canonical_event_type_from_domain(VerifiedEventType.NORMAL_INTERACTION) == CanonicalEventType.NORMAL
    assert canonical_event_type_from_domain(VerifiedEventType.FALL) == CanonicalEventType.UNKNOWN_ANOMALY
    assert not is_production_supported(VerifiedEventType.FALL)
    assert is_production_supported(VerifiedEventType.POSSIBLE_THEFT)


def test_unknown_label_has_deterministic_safe_and_strict_policies() -> None:
    assert canonical_event_type_from_ws_label("future_label") == CanonicalEventType.UNKNOWN_ANOMALY
    with pytest.raises(UnknownEventTypeError, match="bilinmeyen WS olay tipi"):
        canonical_event_type_from_ws_label("future_label", strict=True)


def test_frontend_mirrors_legacy_ws_and_canonical_taxonomy() -> None:
    root = Path(__file__).resolve().parents[3]
    events_ts = (root / "frontend" / "src" / "types" / "events.ts").read_text(encoding="utf-8")
    labels_ts = (root / "frontend" / "src" / "lib" / "labels.ts").read_text(encoding="utf-8")

    for legacy in LegacyWsEventType:
        assert f'"{legacy.value}"' in events_ts
    for canonical in CanonicalEventType:
        assert f'"{canonical.value}"' in events_ts
        assert f"  {canonical.value}:" in labels_ts
