"""Policy ve araçlar arasında sınırlı, izlenebilir yürütme döngüsü."""

from __future__ import annotations

from time import perf_counter

from ..domain.evidence import EvidenceValidationResult, ValidationIssue
from ..tools.protocols import AgentToolset, VlmSchemaError
from .actions import AgentAction
from .policy import RoutingConfig, decide_next_action
from .state import EventAgentState
from .trace import DecisionTraceItem


def _replace(state: EventAgentState, **updates: object) -> EventAgentState:
    data = state.model_dump()
    data.update(updates)
    return EventAgentState.model_validate(data)


class EventOrchestrator:
    def __init__(self, tools: AgentToolset, config: RoutingConfig | None = None) -> None:
        self.tools = tools
        self.config = config or RoutingConfig()

    async def run(self, initial_state: EventAgentState) -> EventAgentState:
        state = _replace(initial_state, policy_version=self.config.policy_version)
        while not state.completed:
            state = await self.step(state)
        return state

    async def step(self, state: EventAgentState) -> EventAgentState:
        decision = decide_next_action(state, self.config)
        if decision.action == AgentAction.COMPLETE:
            return state

        step_number = state.current_step + 1
        started = perf_counter()
        tool_name = decision.expected_tool
        error_code: str | None = None
        success = True
        try:
            next_state = await self._execute(state, decision.action)
        except VlmSchemaError as exc:
            success = False
            error_code = exc.code
            # İlk malformed/schema-invalid yanıt terminal hata değildir: policy
            # yalnız bir strict retry'a izin verir; ikinci hata human review'a gider.
            next_state = _replace(
                state,
                vlm_attempts=state.vlm_attempts + 1,
                strict_schema_used=state.strict_schema_used
                or decision.action == AgentAction.RETRY_VLM_STRICT,
                validation=EvidenceValidationResult(
                    candidate_id=state.candidate_id,
                    schema_valid=False,
                    timestamps_valid=False,
                    evidence_valid=False,
                    validation_errors=[
                        ValidationIssue(
                            code=exc.code,
                            field="vlm_response",
                            message="Yerel VLM çıktısı strict sözleşmeyle eşleşmedi.",
                        )
                    ],
                    validator_version="task-08-vlm-schema-v1",
                ),
            )
        except Exception as exc:
            success = False
            error_code = str(getattr(exc, "code", "TOOL_EXECUTION_FAILED"))
            next_state = _replace(
                state,
                processing_error=f"{type(exc).__name__}: {exc}",
                human_review_required=True,
                completed=True,
            )

        duration_ms = max(0, round((perf_counter() - started) * 1000))
        trace = DecisionTraceItem(
            step=step_number,
            action=decision.action,
            reason=decision.reason,
            policy_rule_id=decision.policy_rule_id,
            tool_name=tool_name,
            input_ref=state.candidate_id,
            output_ref=state.candidate_id,
            success=success,
            error_code=error_code,
            duration_ms=duration_ms,
            policy_version=self.config.policy_version,
        )
        return _replace(
            next_state,
            current_step=step_number,
            decision_trace=[*state.decision_trace, trace],
        )

    async def _execute(
        self, state: EventAgentState, action: AgentAction
    ) -> EventAgentState:
        if action == AgentAction.RUN_CV_ONLY:
            return await self.tools.run_cv_only(state)
        if action == AgentAction.RUN_DENSE_ANALYSIS:
            result = await self.tools.run_dense_analysis(state)
            return _replace(
                result,
                dense_analysis_done=True,
                dense_analysis_count=state.dense_analysis_count + 1,
            )
        if action == AgentAction.EXPAND_CONTEXT:
            result = await self.tools.expand_context(state)
            return _replace(
                result,
                context_expanded=True,
                context_expansion_count=state.context_expansion_count + 1,
            )
        if action in {AgentAction.RUN_VLM, AgentAction.RETRY_VLM_STRICT}:
            strict = action == AgentAction.RETRY_VLM_STRICT
            result = await self.tools.run_vlm(state, strict_schema=strict)
            return _replace(
                result,
                vlm_attempts=state.vlm_attempts + 1,
                strict_schema_used=state.strict_schema_used or strict,
                validation=None,
            )
        if action == AgentAction.VALIDATE_EVIDENCE:
            return await self.tools.validate_evidence(state)
        if action == AgentAction.CONFIRM_EVENT:
            return _replace(state, confirmed=True, completed=True)
        if action == AgentAction.REJECT_EVENT:
            return _replace(state, rejected=True, completed=True)
        if action == AgentAction.REQUEST_HUMAN_REVIEW:
            return _replace(state, human_review_required=True, completed=True)
        if action == AgentAction.PROCESSING_FAILED:
            return _replace(state, processing_failed=True, completed=True)
        raise RuntimeError(f"TASK04_UNSUPPORTED_ACTION: {action}")
