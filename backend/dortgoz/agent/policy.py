"""Araç çağırmayan, deterministik ve sınırlandırılmış routing policy'si."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from ..domain.candidate import CandidateType
from ..domain.evidence import VLMStatus
from .actions import AgentAction
from .state import EventAgentState
from .trace import AgentDecision

TECHNICAL_CANDIDATES = {
    CandidateType.CAMERA_BLACKOUT,
    CandidateType.CAMERA_FREEZE,
    CandidateType.CAMERA_OCCLUSION,
}
CRITICAL_CANDIDATES = {
    CandidateType.POSSIBLE_ASSAULT,
    CandidateType.POSSIBLE_FALL,
    CandidateType.PERSON_ON_GROUND,
    CandidateType.FIRE_SMOKE_CANDIDATE,
    CandidateType.VEHICLE_COLLISION,
}


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(default=14, ge=1, le=14)
    max_vlm_attempts: int = Field(default=2, ge=1, le=2)
    max_context_expansions: int = Field(default=1, ge=0, le=1)
    max_dense_analyses: int = Field(default=1, ge=0, le=1)
    quality_min: float = Field(default=0.35, ge=0, le=1)
    medium_candidate_score: float = Field(default=0.45, ge=0, le=1)
    high_candidate_score: float = Field(default=0.70, ge=0, le=1)
    critical_candidate_score: float = Field(default=0.85, ge=0, le=1)
    cv_only_confidence: float = Field(default=0.92, ge=0, le=1)
    vlm_confirm_confidence: float = Field(default=0.80, ge=0, le=1)
    vlm_reject_confidence: float = Field(default=0.80, ge=0, le=1)
    policy_version: str = "task-03-v1"

    @classmethod
    def from_settings(cls, settings: Settings) -> RoutingConfig:
        return cls(
            max_steps=settings.max_agent_steps,
            max_vlm_attempts=settings.max_vlm_attempts,
            max_context_expansions=settings.max_context_expansions,
            max_dense_analyses=settings.max_dense_analyses,
            quality_min=settings.quality_min,
            medium_candidate_score=settings.medium_candidate_score,
            high_candidate_score=settings.high_candidate_score,
            critical_candidate_score=settings.critical_candidate_score,
            cv_only_confidence=settings.cv_only_confidence,
            vlm_confirm_confidence=settings.vlm_confirm_confidence,
            vlm_reject_confidence=settings.vlm_reject_confidence,
        )


def _decision(
    action: AgentAction,
    reason: str,
    rule: str,
    *,
    priority: int,
    tool: str | None = None,
) -> AgentDecision:
    return AgentDecision(
        action=action,
        reason=reason,
        priority=priority,
        policy_rule_id=rule,
        expected_tool=tool,
    )


def decide_next_action(
    state: EventAgentState, config: RoutingConfig
) -> AgentDecision:
    """State'ten tek bir sonraki eylem üretir; yan etkisi yoktur."""

    if state.completed:
        return _decision(
            AgentAction.COMPLETE, "Terminal karar zaten üretildi.", "P00", priority=1
        )
    if not state.video_processable:
        return _decision(
            AgentAction.PROCESSING_FAILED,
            "Video teknik olarak işlenebilir değil.",
            "P01",
            priority=10,
        )
    if state.processing_error:
        return _decision(
            AgentAction.REQUEST_HUMAN_REVIEW,
            "Araç hatası otomatik hükmü güvensiz kılıyor.",
            "P02",
            priority=10,
        )
    status = state.proposal_status
    confidence = state.proposal_confidence or 0.0
    validation = state.validation
    if validation is not None:
        if (
            status == VLMStatus.CONFIRMED
            and validation.permits_confirmation
            and confidence
            >= (
                config.cv_only_confidence
                if state.vlm_result is None
                else config.vlm_confirm_confidence
            )
        ):
            return _decision(
                AgentAction.CONFIRM_EVENT,
                "Güven eşiği ve evidence kapısı birlikte geçildi.",
                "P11",
                priority=10,
            )
        if (
            status == VLMStatus.REJECTED
            and validation.schema_valid
            and validation.timestamps_valid
            and confidence >= config.vlm_reject_confidence
            and state.candidate.peak_score < config.critical_candidate_score
        ):
            return _decision(
                AgentAction.REJECT_EVENT,
                "Yüksek güvenli ret ve kritik olmayan candidate doğrulandı.",
                "P12",
                priority=8,
            )

    # Son izinli adımı yalnız terminal karara ayır. Böylece tool/recovery çağrısı
    # trace sınırını tüketip state'i kararsız bırakamaz.
    if state.current_step >= config.max_steps - 1:
        return _decision(
            AgentAction.REQUEST_HUMAN_REVIEW,
            "Agent adım bütçesinde yalnız güvenli terminal adım kaldı.",
            "P03",
            priority=10,
        )

    if status is not None and state.validation is None:
        return _decision(
            AgentAction.VALIDATE_EVIDENCE,
            "Model önerisi terminal karardan önce doğrulanmalı.",
            "P10",
            priority=9,
            tool="evidence_validator",
        )

    if validation is not None:
        if validation.unsupported_critical_claim or not validation.critical_evidence_sufficient:
            return _decision(
                AgentAction.REQUEST_HUMAN_REVIEW,
                "Kritik iddia yeterli evidence olmadan otomatik kararda kullanılamaz.",
                "P13",
                priority=10,
            )
        if not validation.schema_valid:
            if state.vlm_attempts < config.max_vlm_attempts:
                return _decision(
                    AgentAction.RETRY_VLM_STRICT,
                    "Geçersiz şema yalnız strict VLM tekrarına izin verir.",
                    "P14",
                    priority=9,
                    tool="vlm_tool",
                )
            return _decision(
                AgentAction.REQUEST_HUMAN_REVIEW,
                "Strict şema tekrarından sonra çıktı hâlâ geçersiz.",
                "P15",
                priority=10,
            )

        cv_vlm_conflict = (
            state.cv_status == VLMStatus.CONFIRMED
            and (state.cv_confidence or 0) >= config.cv_only_confidence
            and state.vlm_result is not None
            and state.vlm_result.status != VLMStatus.CONFIRMED
        )
        if cv_vlm_conflict:
            if state.context_expansion_count < config.max_context_expansions:
                return _decision(
                    AgentAction.EXPAND_CONTEXT,
                    "Yüksek CV güveni ile VLM sonucu çelişti; bağlam genişletilecek.",
                    "P16",
                    priority=9,
                    tool="context_tool",
                )
            if state.dense_analysis_count < config.max_dense_analyses:
                return _decision(
                    AgentAction.RUN_DENSE_ANALYSIS,
                    "CV/VLM çelişkisi geniş bağlamda yoğun CV gerektiriyor.",
                    "P17",
                    priority=9,
                    tool="dense_analysis_tool",
                )
            if state.vlm_attempts < config.max_vlm_attempts:
                return _decision(
                    AgentAction.RETRY_VLM_STRICT,
                    "Çelişki recovery verileriyle ikinci VLM'ye taşınıyor.",
                    "P18",
                    priority=9,
                    tool="vlm_tool",
                )
            return _decision(
                AgentAction.REQUEST_HUMAN_REVIEW,
                "CV/VLM çelişkisi izinli recovery ile çözülemedi.",
                "P19",
                priority=10,
            )

        if status == VLMStatus.UNCERTAIN:
            if state.dense_analysis_count < config.max_dense_analyses:
                return _decision(
                    AgentAction.RUN_DENSE_ANALYSIS,
                    "Belirsiz sonuç önce yoğun CV sinyaliyle desteklenecek.",
                    "P30",
                    priority=8,
                    tool="dense_analysis_tool",
                )
            if state.context_expansion_count < config.max_context_expansions:
                return _decision(
                    AgentAction.EXPAND_CONTEXT,
                    "Yoğun CV sonrası belirsizlik için bağlam genişletilecek.",
                    "P31",
                    priority=8,
                    tool="context_tool",
                )
            if state.vlm_attempts < config.max_vlm_attempts:
                return _decision(
                    AgentAction.RETRY_VLM_STRICT,
                    "Belirsiz sonuç recovery verileriyle son kez sınanacak.",
                    "P32",
                    priority=9,
                    tool="vlm_tool",
                )
            return _decision(
                AgentAction.REQUEST_HUMAN_REVIEW,
                "Belirsiz sonuç için izinli recovery bütçesi tükendi.",
                "P33",
                priority=10,
            )

        if not validation.timestamps_valid or not validation.evidence_valid:
            if state.context_expansion_count < config.max_context_expansions:
                return _decision(
                    AgentAction.EXPAND_CONTEXT,
                    "Zaman veya evidence geçersiz; bağlam genişletilecek.",
                    "P34",
                    priority=8,
                    tool="context_tool",
                )
            if state.vlm_attempts < config.max_vlm_attempts:
                return _decision(
                    AgentAction.RETRY_VLM_STRICT,
                    "Geniş bağlamla strict VLM tekrarı yapılacak.",
                    "P35",
                    priority=9,
                    tool="vlm_tool",
                )
            return _decision(
                AgentAction.REQUEST_HUMAN_REVIEW,
                "Zaman/evidence sorunu ikinci denemede de çözülemedi.",
                "P36",
                priority=10,
            )

        if state.context_expansion_count < config.max_context_expansions:
            return _decision(
                AgentAction.EXPAND_CONTEXT,
                "Eşik altı karar için izinli bağlam genişletmesi kullanılacak.",
                "P37",
                priority=8,
                tool="context_tool",
            )
        if state.vlm_attempts < config.max_vlm_attempts:
            return _decision(
                AgentAction.RETRY_VLM_STRICT,
                "Recovery verileriyle son şemalı VLM denemesi yapılacak.",
                "P38",
                priority=9,
                tool="vlm_tool",
            )
        return _decision(
            AgentAction.REQUEST_HUMAN_REVIEW,
            "İzinli recovery bütçesi tükendi ve karar hâlâ belirsiz.",
            "P39",
            priority=10,
        )

    if (
        state.image_quality < config.quality_min
        and state.dense_analysis_count < config.max_dense_analyses
    ):
        return _decision(
            AgentAction.RUN_DENSE_ANALYSIS,
            "Görüntü kalitesi doğrudan VLM hükmü için düşük.",
            "P40",
            priority=8,
            tool="dense_analysis_tool",
        )
    if state.image_quality < config.quality_min:
        if state.context_expansion_count < config.max_context_expansions:
            return _decision(
                AgentAction.EXPAND_CONTEXT,
                "Yoğun analiz sonrası kalite düşük; bağlam genişletilecek.",
                "P41",
                priority=8,
                tool="context_tool",
            )
        return _decision(
            AgentAction.REQUEST_HUMAN_REVIEW,
            "Düşük kalite dense ve context adımlarıyla giderilemedi.",
            "P42",
            priority=10,
        )
    if state.candidate.candidate_type in TECHNICAL_CANDIDATES:
        return _decision(
            AgentAction.RUN_CV_ONLY,
            "Teknik kamera arızası deterministik CV ile sınanabilir.",
            "P43",
            priority=8,
            tool="cv_tool",
        )
    if (
        config.medium_candidate_score
        <= state.candidate.anomaly_score
        < config.high_candidate_score
        and state.dense_analysis_count < config.max_dense_analyses
    ):
        return _decision(
            AgentAction.RUN_DENSE_ANALYSIS,
            "Orta güvenli candidate yoğun CV ile zenginleştirilecek.",
            "P44",
            priority=7,
            tool="dense_analysis_tool",
        )
    if state.vlm_attempts < config.max_vlm_attempts:
        critical = (
            state.candidate.candidate_type in CRITICAL_CANDIDATES
            and state.candidate.peak_score >= config.critical_candidate_score
        )
        return _decision(
            AgentAction.RUN_VLM,
            (
                "Kritik eşik üstü candidate öncelikli yerel VLM gerektiriyor."
                if critical
                else "Bağlamsal candidate yerel VLM doğrulaması gerektiriyor."
            ),
            "P45" if critical else "P46",
            priority=9 if critical else 7,
            tool="vlm_tool",
        )
    return _decision(
        AgentAction.REQUEST_HUMAN_REVIEW,
        "Otomatik doğrulama üretilemedi.",
        "P47",
        priority=10,
    )
