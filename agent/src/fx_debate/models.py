"""Public structured contracts for one FX Debate run."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ResolvedFxDebateRequest(BaseModel):
    """Planner-owned, deterministic input accepted by ``run_fx_debate``."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["resolved"]
    asset_class: Literal["fx"]
    instrument_type: Literal["spot"]
    pair_class: Literal["major", "minor", "exotic"]
    canonical_symbol: str
    display_symbol: str
    base_currency: str
    quote_currency: str
    requested_base_currency: str
    requested_quote_currency: str
    inverted: bool
    horizon: str
    timeframe: str
    # The upstream handoff keeps the original three variables.  The field is
    # optional because Planner integrations may persist the objective outside
    # the resolved identity object.
    goal: str | None = None

    @field_validator(
        "canonical_symbol",
        "base_currency",
        "quote_currency",
        "requested_base_currency",
        "requested_quote_currency",
    )
    @classmethod
    def normalize_codes(cls, value: str) -> str:
        """Normalize Planner codes without performing symbol resolution."""
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("currency and symbol codes must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_pair_direction(self) -> "ResolvedFxDebateRequest":
        """Require canonical and user direction fields to agree exactly."""
        expected_canonical = self.base_currency + self.quote_currency
        if self.canonical_symbol != expected_canonical:
            raise ValueError(
                "canonical_symbol must equal base_currency + quote_currency"
            )

        requested = self.requested_base_currency + self.requested_quote_currency
        expected_requested = (
            self.quote_currency + self.base_currency
            if self.inverted
            else expected_canonical
        )
        if requested != expected_requested:
            raise ValueError(
                "requested currencies disagree with canonical pair and inverted flag"
            )
        return self


class RunOptions(BaseModel):
    """Optional controls that do not alter the resolved instrument semantics."""

    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    as_of: datetime | None = None
    risk_profile: Literal["conservative", "balanced", "aggressive"] = "balanced"
    language: Literal["zh-CN"] = "zh-CN"

    @field_validator("as_of")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous local datetimes at the public interface."""
        if value is not None and value.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        return value


class EvidenceContext(BaseModel):
    """Immutable data scope shared by all five agents in one run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_context_id: str
    request_id: str
    canonical_symbol: str
    display_symbol: str
    base_currency: str
    quote_currency: str
    requested_base_currency: str
    requested_quote_currency: str
    inverted: bool
    pair_class: Literal["major", "minor", "exotic"]
    horizon: str
    goal: str | None = None
    horizon_days: int = Field(ge=1, le=90)
    timeframes: list[Literal["4H", "1D"]]
    as_of: datetime
    risk_profile: Literal["conservative", "balanced", "aggressive"]
    provider_priority: list[str]
    market_start_time: datetime
    news_start_time: datetime
    market_bar_limit_per_timeframe: dict[str, int]
    macro_observation_limit: int = Field(ge=1, le=500)
    news_limit: int = Field(ge=1, le=200)


class EvidenceItem(BaseModel):
    """Smallest traceable fact that an Agent claim may reference."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    evidence_context_id: str
    evidence_family_id: str | None = None
    domain: Literal["market", "technical", "macro", "news"]
    name: str
    timeframe: Literal["4H", "1D"] | None = None
    value: Any
    unit: str
    observation_time: datetime
    available_time: datetime
    source: str
    source_identifier: str | None = None
    dataset_id: str | None = None
    source_table: str
    source_record_ids: list[str] = Field(default_factory=list)
    calculation: str | None = None
    quality_status: Literal["fresh", "stale", "partial", "abnormal"]
    notes: str | None = None


class EvidenceError(BaseModel):
    """Machine-readable blocking error returned by an evidence Tool."""

    model_config = ConfigDict(extra="forbid")

    code: str
    path: str = ""
    message: str


class EvidenceQueryResult(BaseModel):
    """Stable JSON envelope returned by the FX evidence Tools."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    status: Literal["complete", "partial", "insufficient_evidence", "error"]
    evidence_context_id: str
    query_id: str
    data_as_of: datetime | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    errors: list[EvidenceError] = Field(default_factory=list)
