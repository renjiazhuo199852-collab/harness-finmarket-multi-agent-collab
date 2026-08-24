"""Deterministic Swarm authorization derived from one raw user message."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.fx_debate.router import FxRouteDecision, route_fx_prompt


_POSITIVE_PATTERNS = (
    re.compile(r"团队"),
    re.compile(r"多\s*(?:个\s*)?智能体"),
    re.compile(r"多\s*(?:个\s*)?agent\b", re.IGNORECASE),
    re.compile(r"辩论"),
    re.compile(r"协作分析"),
    re.compile(r"\bfx\s*debate\b", re.IGNORECASE),
    re.compile(r"\bswarm\b", re.IGNORECASE),
)
_NEGATIVE_PATTERN = re.compile(
    r"(?:不要|不使用|不用|无需|禁止(?:调用)?|别让|不要让)"
    r".{0,12}"
    r"(?:启动\s*)?(?:团队|多\s*(?:个\s*)?智能体|多\s*(?:个\s*)?agent\b|"
    r"辩论|协作分析|fx\s*debate\b|swarm\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SwarmAuthorization:
    """Immutable authorization and route decision for one AgentLoop attempt."""

    raw_user_content: str
    authorized: bool
    fx_decision: FxRouteDecision | None = None


def build_swarm_authorization(raw_user_content: str) -> SwarmAuthorization:
    """Authorize team tools from only the current raw user message."""
    prompt = raw_user_content if isinstance(raw_user_content, str) else ""
    if _NEGATIVE_PATTERN.search(prompt):
        return SwarmAuthorization(raw_user_content=prompt, authorized=False)

    authorized = any(pattern.search(prompt) for pattern in _POSITIVE_PATTERNS)
    if not authorized:
        return SwarmAuthorization(raw_user_content=prompt, authorized=False)

    return SwarmAuthorization(
        raw_user_content=prompt,
        authorized=True,
        fx_decision=route_fx_prompt(prompt),
    )
