from typing import Any

EFFORT_LADDER = ("low", "medium", "xhigh")
EFFORT_ALIASES = {"high": "xhigh"}
EFFORT_OFF = "kapali"
VALID_EFFORTS = ("", EFFORT_OFF, *EFFORT_LADDER)


def validate_effort(effort: str) -> str:
    effort = EFFORT_ALIASES.get(effort, effort)
    if effort not in VALID_EFFORTS:
        raise ValueError(
            f"geçersiz düşünme kademesi {effort!r} — geçerli değerler: "
            f"{', '.join(repr(v) for v in VALID_EFFORTS)}")
    return effort


def thinking_on(*, think: bool, effort: str = "") -> bool:
    effort = validate_effort(effort)
    if effort == EFFORT_OFF:
        return False
    if effort in EFFORT_LADDER:
        return True
    return think


def thinking_extra(*, think: bool, effort: str = "", budget: int = 2500) -> dict[str, Any]:
    effort = validate_effort(effort)
    if effort in EFFORT_LADDER:
        return {"chat_template_kwargs": {"reasoning_effort": effort},
                "reasoning_budget_tokens": budget}
    if effort == EFFORT_OFF:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {"chat_template_kwargs": {"enable_thinking": think},
            **({"reasoning_budget_tokens": budget} if think else {})}
