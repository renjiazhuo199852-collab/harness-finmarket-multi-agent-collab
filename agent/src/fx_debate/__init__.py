"""Structured multi-pair FX Debate models, evidence adapters, and orchestration."""

from src.fx_debate.context import build_evidence_context
from src.fx_debate.contracts import (
    AgentArgument,
    FinalDecision,
    HypothesisArgumentV2,
    RelativeStateV2,
    RiskReview,
)
from src.fx_debate.evidence_factory import EvidenceBundle, FxEvidenceFactory
from src.fx_debate.evidence_sources import (
    AiSearchFxEvidenceSource,
    ExcelFxEvidenceSource,
    FxEvidenceSource,
    ReaderFxEvidenceSource,
)
from src.fx_debate.data_query_agent import (
    AiSearchClient,
    DataSearchClient,
    DataQueryPlan,
    FxDataQueryAgent,
    FxDataServiceError,
    McpAiSearchClient,
)
from src.fx_debate.models import (
    EvidenceContext,
    EvidenceError,
    EvidenceItem,
    EvidenceQueryResult,
    ResolvedFxDebateRequest,
    RunOptions,
)
from src.fx_debate.request_adapter import (
    AdaptedFxPairDebateRequest,
    DeterministicFxSymbolResolver,
    FxSymbolCandidate,
    FxPairDebateRequest,
    FxSymbolResolver,
    adapt_fx_pair_debate_request,
)
from src.fx_debate.router import FxRouteDecision, FxRouter, route_fx_prompt

__all__ = [
    "EvidenceContext",
    "EvidenceError",
    "EvidenceItem",
    "EvidenceQueryResult",
    "AgentArgument",
    "HypothesisArgumentV2",
    "RelativeStateV2",
    "RiskReview",
    "FinalDecision",
    "ResolvedFxDebateRequest",
    "RunOptions",
    "EvidenceBundle",
    "FxEvidenceFactory",
    "FxEvidenceSource",
    "ExcelFxEvidenceSource",
    "ReaderFxEvidenceSource",
    "AiSearchFxEvidenceSource",
    "AiSearchClient",
    "DataSearchClient",
    "DataQueryPlan",
    "FxDataQueryAgent",
    "FxDataServiceError",
    "McpAiSearchClient",
    "build_evidence_context",
    "AdaptedFxPairDebateRequest",
    "DeterministicFxSymbolResolver",
    "FxSymbolCandidate",
    "FxPairDebateRequest",
    "FxSymbolResolver",
    "adapt_fx_pair_debate_request",
    "FxRouteDecision",
    "FxRouter",
    "route_fx_prompt",
]
