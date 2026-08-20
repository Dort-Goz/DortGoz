from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CanonicalEventType(StrEnum):

    NORMAL = "normal"
    UNCERTAIN = "uncertain"
    UNKNOWN_ANOMALY = "unknown_anomaly"
    PHYSICAL_FIGHT = "physical_fight"
    ASSAULT = "assault"
    POSSIBLE_THEFT = "possible_theft"
    POSSIBLE_ARMED_INCIDENT = "possible_armed_incident"
    FIRE_SMOKE = "fire_smoke"
    EXPLOSION = "explosion"
    VEHICLE_COLLISION = "vehicle_collision"
    VANDALISM = "vandalism"


PRODUCTION_SUPPORTED_EVENT_TYPES = frozenset(CanonicalEventType)

REQUIRES_HUMAN_REVIEW_EVENT_TYPES = frozenset(
    {CanonicalEventType.POSSIBLE_ARMED_INCIDENT}
)


class LegacyWsEventType(StrEnum):

    FIGHT = "kavga"
    ASSAULT = "saldiri"
    THEFT = "hirsizlik"
    ARMED_INCIDENT = "silahli_olay"
    FIRE = "yangin"
    EXPLOSION = "patlama"
    VEHICLE_COLLISION = "arac_kazasi"
    VANDALISM = "vandalizm"
    NORMAL = "normal"
    UNKNOWN = "bilinmeyen"


class VerifiedEventType(StrEnum):

    NORMAL = CanonicalEventType.NORMAL.value
    UNCERTAIN = CanonicalEventType.UNCERTAIN.value
    UNKNOWN_ANOMALY = CanonicalEventType.UNKNOWN_ANOMALY.value
    PHYSICAL_FIGHT = CanonicalEventType.PHYSICAL_FIGHT.value
    ASSAULT = CanonicalEventType.ASSAULT.value
    POSSIBLE_THEFT = CanonicalEventType.POSSIBLE_THEFT.value
    POSSIBLE_ARMED_INCIDENT = CanonicalEventType.POSSIBLE_ARMED_INCIDENT.value
    FIRE_SMOKE = CanonicalEventType.FIRE_SMOKE.value
    EXPLOSION = CanonicalEventType.EXPLOSION.value
    VEHICLE_COLLISION = CanonicalEventType.VEHICLE_COLLISION.value
    VANDALISM = CanonicalEventType.VANDALISM.value

    NORMAL_INTERACTION = "normal_interaction"
    PLAY_FIGHTING = "play_fighting"
    FALL = "fall"
    CONTROLLED_SITTING = "controlled_sitting"
    PERSON_ON_GROUND = "person_on_ground"
    CAMERA_BLACKOUT = "camera_blackout"
    CAMERA_FREEZE = "camera_freeze"
    CAMERA_OCCLUSION = "camera_occlusion"


LEGACY_WS_TO_CANONICAL: dict[LegacyWsEventType, CanonicalEventType] = {
    LegacyWsEventType.FIGHT: CanonicalEventType.PHYSICAL_FIGHT,
    LegacyWsEventType.ASSAULT: CanonicalEventType.ASSAULT,
    LegacyWsEventType.THEFT: CanonicalEventType.POSSIBLE_THEFT,
    LegacyWsEventType.ARMED_INCIDENT: CanonicalEventType.POSSIBLE_ARMED_INCIDENT,
    LegacyWsEventType.FIRE: CanonicalEventType.FIRE_SMOKE,
    LegacyWsEventType.EXPLOSION: CanonicalEventType.EXPLOSION,
    LegacyWsEventType.VEHICLE_COLLISION: CanonicalEventType.VEHICLE_COLLISION,
    LegacyWsEventType.VANDALISM: CanonicalEventType.VANDALISM,
    LegacyWsEventType.NORMAL: CanonicalEventType.NORMAL,
    LegacyWsEventType.UNKNOWN: CanonicalEventType.UNKNOWN_ANOMALY,
}

CANONICAL_TO_LEGACY_WS: dict[CanonicalEventType, LegacyWsEventType] = {
    CanonicalEventType.NORMAL: LegacyWsEventType.NORMAL,
    CanonicalEventType.UNCERTAIN: LegacyWsEventType.UNKNOWN,
    CanonicalEventType.UNKNOWN_ANOMALY: LegacyWsEventType.UNKNOWN,
    CanonicalEventType.PHYSICAL_FIGHT: LegacyWsEventType.FIGHT,
    CanonicalEventType.ASSAULT: LegacyWsEventType.ASSAULT,
    CanonicalEventType.POSSIBLE_THEFT: LegacyWsEventType.THEFT,
    CanonicalEventType.POSSIBLE_ARMED_INCIDENT: LegacyWsEventType.ARMED_INCIDENT,
    CanonicalEventType.FIRE_SMOKE: LegacyWsEventType.FIRE,
    CanonicalEventType.EXPLOSION: LegacyWsEventType.EXPLOSION,
    CanonicalEventType.VEHICLE_COLLISION: LegacyWsEventType.VEHICLE_COLLISION,
    CanonicalEventType.VANDALISM: LegacyWsEventType.VANDALISM,
}

_DOMAIN_TO_CANONICAL: dict[VerifiedEventType, CanonicalEventType] = {
    VerifiedEventType.NORMAL: CanonicalEventType.NORMAL,
    VerifiedEventType.UNCERTAIN: CanonicalEventType.UNCERTAIN,
    VerifiedEventType.UNKNOWN_ANOMALY: CanonicalEventType.UNKNOWN_ANOMALY,
    VerifiedEventType.PHYSICAL_FIGHT: CanonicalEventType.PHYSICAL_FIGHT,
    VerifiedEventType.ASSAULT: CanonicalEventType.ASSAULT,
    VerifiedEventType.POSSIBLE_THEFT: CanonicalEventType.POSSIBLE_THEFT,
    VerifiedEventType.POSSIBLE_ARMED_INCIDENT: CanonicalEventType.POSSIBLE_ARMED_INCIDENT,
    VerifiedEventType.FIRE_SMOKE: CanonicalEventType.FIRE_SMOKE,
    VerifiedEventType.EXPLOSION: CanonicalEventType.EXPLOSION,
    VerifiedEventType.VEHICLE_COLLISION: CanonicalEventType.VEHICLE_COLLISION,
    VerifiedEventType.VANDALISM: CanonicalEventType.VANDALISM,
    VerifiedEventType.NORMAL_INTERACTION: CanonicalEventType.NORMAL,
    VerifiedEventType.PLAY_FIGHTING: CanonicalEventType.UNCERTAIN,
    VerifiedEventType.FALL: CanonicalEventType.UNKNOWN_ANOMALY,
    VerifiedEventType.CONTROLLED_SITTING: CanonicalEventType.UNKNOWN_ANOMALY,
    VerifiedEventType.PERSON_ON_GROUND: CanonicalEventType.UNKNOWN_ANOMALY,
    VerifiedEventType.CAMERA_BLACKOUT: CanonicalEventType.UNKNOWN_ANOMALY,
    VerifiedEventType.CAMERA_FREEZE: CanonicalEventType.UNKNOWN_ANOMALY,
    VerifiedEventType.CAMERA_OCCLUSION: CanonicalEventType.UNKNOWN_ANOMALY,
}

_DATASET_SOURCE_LABEL_TO_CANONICAL: dict[str, CanonicalEventType] = {
    "stealing": CanonicalEventType.POSSIBLE_THEFT,
    "shoplifting": CanonicalEventType.POSSIBLE_THEFT,
    "robbery": CanonicalEventType.POSSIBLE_THEFT,
    "burglary": CanonicalEventType.POSSIBLE_THEFT,
    "fighting": CanonicalEventType.PHYSICAL_FIGHT,
    "assault": CanonicalEventType.ASSAULT,
    "arson": CanonicalEventType.FIRE_SMOKE,
    "explosion": CanonicalEventType.EXPLOSION,
    "roadaccidents": CanonicalEventType.VEHICLE_COLLISION,
    "vandalism": CanonicalEventType.VANDALISM,
    "normal": CanonicalEventType.NORMAL,
}

CANONICAL_UI_LABEL_TR: dict[CanonicalEventType, str] = {
    CanonicalEventType.NORMAL: "olağan",
    CanonicalEventType.UNCERTAIN: "belirsiz",
    CanonicalEventType.UNKNOWN_ANOMALY: "sınıflandırılamayan anomali",
    CanonicalEventType.PHYSICAL_FIGHT: "fiziksel kavga",
    CanonicalEventType.ASSAULT: "saldırı şüphesi",
    CanonicalEventType.POSSIBLE_THEFT: "olası hırsızlık",
    CanonicalEventType.POSSIBLE_ARMED_INCIDENT: "silaha benzer nesne içeren olası olay",
    CanonicalEventType.FIRE_SMOKE: "yangın veya duman",
    CanonicalEventType.EXPLOSION: "patlama",
    CanonicalEventType.VEHICLE_COLLISION: "araç çarpışması",
    CanonicalEventType.VANDALISM: "vandalizm",
}


class UnknownEventTypeError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetEventMapping:

    source_label: str
    event_type: CanonicalEventType
    matched: bool


def canonical_event_type_from_ws_label(
    label: str | LegacyWsEventType, *, strict: bool = False
) -> CanonicalEventType:

    try:
        return LEGACY_WS_TO_CANONICAL[LegacyWsEventType(label)]
    except ValueError as exc:
        if strict:
            raise UnknownEventTypeError(f"bilinmeyen WS olay tipi: {label}") from exc
        return CanonicalEventType.UNKNOWN_ANOMALY


def legacy_ws_label_from_canonical(
    event_type: str | CanonicalEventType,
) -> LegacyWsEventType:

    try:
        return CANONICAL_TO_LEGACY_WS[CanonicalEventType(event_type)]
    except ValueError as exc:
        raise UnknownEventTypeError(
            f"bilinmeyen canonical olay tipi: {event_type}"
        ) from exc


def canonical_event_type_from_domain(
    event_type: str | VerifiedEventType, *, strict: bool = False
) -> CanonicalEventType:

    try:
        return _DOMAIN_TO_CANONICAL[VerifiedEventType(event_type)]
    except ValueError as exc:
        if strict:
            raise UnknownEventTypeError(f"bilinmeyen domain olay tipi: {event_type}") from exc
        return CanonicalEventType.UNKNOWN_ANOMALY


def map_dataset_source_label(source_label: str) -> DatasetEventMapping:

    original = source_label.strip()
    if not original:
        raise ValueError("dataset source_label boş olamaz")
    event_type = _DATASET_SOURCE_LABEL_TO_CANONICAL.get(original.casefold())
    return DatasetEventMapping(
        source_label=original,
        event_type=event_type or CanonicalEventType.UNKNOWN_ANOMALY,
        matched=event_type is not None,
    )


def is_production_supported(event_type: str | VerifiedEventType) -> bool:

    try:
        return VerifiedEventType(event_type).value in {
            item.value for item in PRODUCTION_SUPPORTED_EVENT_TYPES
        }
    except ValueError:
        return False


def requires_human_review(event_type: str | VerifiedEventType) -> bool:

    return canonical_event_type_from_domain(event_type) in REQUIRES_HUMAN_REVIEW_EVENT_TYPES


__all__ = [
    "CANONICAL_TO_LEGACY_WS",
    "CANONICAL_UI_LABEL_TR",
    "LEGACY_WS_TO_CANONICAL",
    "PRODUCTION_SUPPORTED_EVENT_TYPES",
    "REQUIRES_HUMAN_REVIEW_EVENT_TYPES",
    "CanonicalEventType",
    "DatasetEventMapping",
    "LegacyWsEventType",
    "UnknownEventTypeError",
    "VerifiedEventType",
    "canonical_event_type_from_domain",
    "canonical_event_type_from_ws_label",
    "legacy_ws_label_from_canonical",
    "is_production_supported",
    "map_dataset_source_label",
    "requires_human_review",
]
