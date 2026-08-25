"""Versioned, review-first customization for swarm agent prompts and skills.

The module keeps bundled presets immutable.  User overrides are merged at the
``build_run_from_preset`` seam, so every new run receives a reproducible agent
snapshot while already-running runs remain unchanged.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.agent.skills import SkillsLoader


USER_AGENT_OVERRIDES_DIR = Path.home() / ".vibe-trading" / "swarm" / "agent_overrides"
MAX_INSTRUCTION_LENGTH = 4_000
MAX_PROMPT_LENGTH = 64_000
MAX_SKILL_LENGTH = 64_000
MAX_SKILLS = 64
UNSAFE_PROMPT_MARKERS = (
    "ignore previous instructions",
    "ignore all instructions",
    "disable safety",
    "bypass safety",
    "fabricate evidence",
    "invent evidence",
    "without citing evidence",
)


class AgentCandidate(BaseModel):
    """The only editable portion of an agent configuration."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    skills: list[str] = Field(default_factory=list)
    skill_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("system_prompt")
    @classmethod
    def prompt_size(cls, value: str) -> str:
        if len(value) > MAX_PROMPT_LENGTH:
            raise ValueError(f"system_prompt exceeds {MAX_PROMPT_LENGTH} characters")
        return value

    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if len(normalized) > MAX_SKILLS:
            raise ValueError(f"skills exceeds {MAX_SKILLS} entries")
        return normalized

    @field_validator("skill_overrides")
    @classmethod
    def skill_size(cls, value: dict[str, str]) -> dict[str, str]:
        for name, body in value.items():
            if len(body) > MAX_SKILL_LENGTH:
                raise ValueError(f"skill override {name!r} exceeds {MAX_SKILL_LENGTH} characters")
        return value


class AgentOverride(AgentCandidate):
    """Persisted effective values for one preset agent."""

    preset_name: str
    agent_id: str
    revision: str
    updated_at: str


class ProposalFinding(BaseModel):
    """Structured explanation for a model review finding."""

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    message: str | None = None
    description: str | None = None


class ProposalCheck(BaseModel):
    """Structured deterministic/model check reported by a review."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    passed: bool | None = None
    result: str | None = None
    message: str | None = None
    description: str | None = None


class ProposalReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool = False
    risk_level: str = "high"
    findings: list[str | ProposalFinding] = Field(default_factory=list)
    checks: list[str | ProposalCheck] = Field(default_factory=list)


class AgentEditProposal(BaseModel):
    proposal_id: str
    preset_name: str
    agent_id: str
    instruction: str
    base_revision: str
    candidate: AgentCandidate
    diff: dict[str, Any]
    review: ProposalReview
    created_at: str
    session_id: str | None = None


class CustomizationError(ValueError):
    """Base error for user-facing customization validation failures."""


class RevisionConflict(CustomizationError):
    """Raised when a caller edits a stale effective configuration."""


class AgentCustomizationService:
    """Deep interface for agent editor, persistence, and effective resolution."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or USER_AGENT_OVERRIDES_DIR
        self._cache: dict[tuple[str, str], AgentCandidate] = {}
        self._proposals: dict[str, AgentEditProposal] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Paths and canonical content
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_segment(value: str, label: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
            raise CustomizationError(f"invalid {label}")
        return cleaned

    def _override_path(self, preset_name: str, agent_id: str) -> Path:
        preset = self._validate_segment(preset_name, "preset_name")
        agent = self._validate_segment(agent_id, "agent_id")
        return self.root / preset / f"{agent}.json"

    def _history_path(self, preset_name: str, agent_id: str) -> Path:
        path = self._override_path(preset_name, agent_id)
        return path.with_name(f"{path.stem}.history.jsonl")

    @staticmethod
    def _canonical(candidate: AgentCandidate) -> str:
        return json.dumps(candidate.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def revision_for(cls, candidate: AgentCandidate) -> str:
        return hashlib.sha256(cls._canonical(candidate).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Default/effective resolution
    # ------------------------------------------------------------------

    def _default_candidate(self, preset_name: str, agent_id: str) -> AgentCandidate:
        from src.swarm.presets import load_preset

        data = load_preset(preset_name)
        for raw in data.get("agents", []):
            if raw.get("id") == agent_id:
                return AgentCandidate(
                    system_prompt=str(raw.get("system_prompt", "")),
                    skills=list(raw.get("skills", []) or []),
                    skill_overrides={},
                )
        raise FileNotFoundError(f"Agent {agent_id!r} not found in preset {preset_name!r}")

    def _read_override(self, preset_name: str, agent_id: str) -> AgentOverride | None:
        path = self._override_path(preset_name, agent_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            override = AgentOverride.model_validate(payload)
            candidate = AgentCandidate(
                system_prompt=override.system_prompt,
                skills=override.skills,
                skill_overrides=override.skill_overrides,
            )
            if override.revision != self.revision_for(candidate):
                raise CustomizationError(f"revision mismatch for {preset_name}/{agent_id}")
            return override
        except Exception as exc:  # malformed overrides are reported by reload/apply
            fallback = self._read_last_valid_history(preset_name, agent_id)
            if fallback is not None:
                return fallback
            raise CustomizationError(f"invalid agent override for {preset_name}/{agent_id}: {exc}") from exc

    def _read_last_valid_history(self, preset_name: str, agent_id: str) -> AgentOverride | None:
        """Recover the last atomically applied candidate after file corruption."""
        history = self._history_path(preset_name, agent_id)
        if not history.is_file():
            return None
        for line in reversed(history.read_text(encoding="utf-8").splitlines()):
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    continue
                if item.get("action") != "apply" or not isinstance(item.get("candidate"), dict):
                    continue
                candidate = AgentCandidate.model_validate(item["candidate"])
                revision = self.revision_for(candidate)
                if item.get("revision") != revision:
                    continue
                return AgentOverride(
                    preset_name=preset_name,
                    agent_id=agent_id,
                    revision=revision,
                    updated_at=str(item.get("at") or ""),
                    **candidate.model_dump(),
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return None

    def effective_candidate(self, preset_name: str, agent_id: str) -> AgentCandidate:
        key = (preset_name, agent_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached.model_copy(deep=True)
            default = self._default_candidate(preset_name, agent_id)
            override = self._read_override(preset_name, agent_id)
            effective = AgentCandidate(
                system_prompt=override.system_prompt if override else default.system_prompt,
                skills=list(override.skills if override else default.skills),
                skill_overrides=dict(override.skill_overrides if override else {}),
            )
            self._validate_candidate(effective)
            self._cache[key] = effective
            return effective.model_copy(deep=True)

    def default_candidate(self, preset_name: str, agent_id: str) -> AgentCandidate:
        return self._default_candidate(preset_name, agent_id)

    def current_revision(self, preset_name: str, agent_id: str) -> str:
        return self.revision_for(self.effective_candidate(preset_name, agent_id))

    def apply_overrides_to_preset_data(self, preset_name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of preset data with valid agent overrides merged."""
        result = json.loads(json.dumps(data, ensure_ascii=False))
        for raw in result.get("agents", []):
            agent_id = str(raw.get("id", ""))
            if not agent_id:
                continue
            override = self._read_override(preset_name, agent_id)
            if override is None:
                continue
            raw["system_prompt"] = override.system_prompt
            raw["skills"] = list(override.skills)
            raw["skill_overrides"] = dict(override.skill_overrides)
            raw["config_revision"] = override.revision
        return result

    def list_skills(self) -> list[dict[str, str]]:
        loader = SkillsLoader()
        return [
            {"name": skill.name, "description": skill.description, "category": skill.category}
            for skill in loader.skills
        ]

    @staticmethod
    def _skill_contents(candidate: AgentCandidate) -> dict[str, str]:
        loader = SkillsLoader(overrides=candidate.skill_overrides)
        return {name: loader.get_content(name) for name in candidate.skills}

    def editor_payload(self, preset_name: str, agent_id: str) -> dict[str, Any]:
        default = self.default_candidate(preset_name, agent_id)
        effective = self.effective_candidate(preset_name, agent_id)
        override = self._read_override(preset_name, agent_id)
        return {
            "preset_name": preset_name,
            "agent_id": agent_id,
            "role": self._agent_role(preset_name, agent_id),
            "source": "user_override" if override else "default",
            "revision": self.revision_for(effective),
            "updated_at": override.updated_at if override else None,
            "effective": effective.model_dump(),
            "defaults": default.model_dump(),
            "effective_skill_contents": self._skill_contents(effective),
            "default_skill_contents": self._skill_contents(default),
            "available_skills": self.list_skills(),
        }

    def _agent_role(self, preset_name: str, agent_id: str) -> str:
        from src.swarm.presets import load_preset

        for raw in load_preset(preset_name).get("agents", []):
            if raw.get("id") == agent_id:
                return str(raw.get("role", ""))
        raise FileNotFoundError(f"Agent {agent_id!r} not found in preset {preset_name!r}")

    # ------------------------------------------------------------------
    # Proposal generation and review
    # ------------------------------------------------------------------

    def propose(
        self,
        preset_name: str,
        agent_id: str,
        instruction: str,
        base_revision: str,
        session_id: str | None = None,
    ) -> AgentEditProposal:
        instruction = (instruction or "").strip()
        if not instruction or len(instruction) > MAX_INSTRUCTION_LENGTH:
            raise CustomizationError(f"instruction must be 1-{MAX_INSTRUCTION_LENGTH} characters")
        current = self.effective_candidate(preset_name, agent_id)
        actual_revision = self.revision_for(current)
        if base_revision != actual_revision:
            raise RevisionConflict("agent configuration changed; reload before proposing")

        previous = self._proposals.get(session_id or "") if session_id else None
        candidate = self._model_candidate(preset_name, agent_id, current, instruction, previous)
        self._validate_candidate(candidate)
        diff = self._diff(current, candidate)
        review = self._model_review(preset_name, agent_id, current, candidate, diff)
        proposal = AgentEditProposal(
            proposal_id=f"agent-edit-{uuid.uuid4().hex[:12]}",
            preset_name=preset_name,
            agent_id=agent_id,
            instruction=instruction,
            base_revision=base_revision,
            candidate=candidate,
            diff=diff,
            review=review,
            created_at=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
        )
        with self._lock:
            self._proposals[proposal.proposal_id] = proposal
            if session_id:
                self._proposals[session_id] = proposal
        return proposal

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any]:
        text = (content or "").strip()
        decoder = json.JSONDecoder()

        # Providers occasionally add a short explanation or wrap the payload
        # in a Markdown code fence despite the JSON-only instruction. Accept
        # those transport decorations while keeping the decoded value strict.
        candidates = [text]
        candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*\n?(.*?)```", text, re.IGNORECASE | re.DOTALL))
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value

        # Decode the first complete object embedded in prose. raw_decode is
        # deliberately used instead of regex balancing so braces inside JSON
        # strings remain valid.
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                value, _end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value

        raise CustomizationError("model returned invalid JSON")

    def _call_model(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        from src.providers.chat import ChatLLM

        response = ChatLLM().chat(messages, timeout=90)
        return self._extract_json(response.content or "")

    @staticmethod
    def _normalize_model_candidate_payload(
        raw: Mapping[str, Any],
        current: AgentCandidate | None = None,
    ) -> dict[str, Any]:
        """Normalize provider-shaped skill bodies before strict validation.

        The editor contract stores complete skill content as strings, but some
        providers naturally return a structured object for a requested skill
        edit.  Keep this compatibility shim at the model boundary only; the
        persisted/API candidate model remains strict and cannot accept nested
        arbitrary values.
        """
        normalized = dict(raw)
        # Coding models sometimes wrap the requested object in a transport
        # envelope, or return an empty object after a tool/JSON negotiation.
        # Unwrap known envelopes and preserve the current candidate for fields
        # that were omitted. This makes a partial model response a no-op rather
        # than an accidental prompt/skill deletion.
        for envelope in ("candidate", "data", "result", "output"):
            nested = normalized.get(envelope)
            if isinstance(nested, Mapping):
                normalized = dict(nested)
                break
        aliases = {
            "systemPrompt": "system_prompt",
            "skillOverrides": "skill_overrides",
        }
        for source, target in aliases.items():
            if target not in normalized and source in normalized:
                normalized[target] = normalized[source]
        if current is not None:
            defaults = current.model_dump()
            if not isinstance(normalized.get("system_prompt"), str) or not normalized["system_prompt"].strip():
                normalized["system_prompt"] = defaults["system_prompt"]
            if not isinstance(normalized.get("skills"), list):
                normalized["skills"] = defaults["skills"]
            if not isinstance(normalized.get("skill_overrides"), Mapping):
                normalized["skill_overrides"] = defaults["skill_overrides"]
        overrides = normalized.get("skill_overrides")
        if not isinstance(overrides, Mapping):
            return normalized

        converted: dict[str, str] = {}
        for name, body in overrides.items():
            skill_name = str(name).strip()
            if not skill_name:
                raise CustomizationError("model returned an empty skill override name")
            if isinstance(body, str):
                converted[skill_name] = body
                continue
            if isinstance(body, Mapping):
                for content_key in ("content", "body", "text", "markdown"):
                    content = body.get(content_key)
                    if isinstance(content, str):
                        converted[skill_name] = content
                        break
                else:
                    converted[skill_name] = json.dumps(
                        body,
                        ensure_ascii=False,
                        sort_keys=True,
                        indent=2,
                    )
                continue
            if isinstance(body, list):
                converted[skill_name] = json.dumps(body, ensure_ascii=False, indent=2)
                continue
            raise CustomizationError(
                f"model returned invalid skill override for {skill_name!r}; expected string or object"
            )

        normalized["skill_overrides"] = converted
        return normalized

    @staticmethod
    def _has_candidate_fields(raw: Mapping[str, Any]) -> bool:
        """Return whether a provider response contains an actual candidate."""
        payload: Mapping[str, Any] = raw
        for envelope in ("candidate", "data", "result", "output"):
            nested = payload.get(envelope)
            if isinstance(nested, Mapping):
                payload = nested
                break
        return any(key in payload for key in ("system_prompt", "systemPrompt", "skills", "skill_overrides", "skillOverrides"))

    def _model_candidate(
        self,
        preset_name: str,
        agent_id: str,
        current: AgentCandidate,
        instruction: str,
        previous: AgentEditProposal | None,
    ) -> AgentCandidate:
        context = previous.candidate.model_dump() if previous else current.model_dump()
        payload = {
            "role": self._agent_role(preset_name, agent_id),
            "current": context,
            "available_skills": self.list_skills(),
            "instruction": instruction,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是金融研究智能体配置编辑助手。请用中文生成候选修改方案，只返回 JSON，"
                    "字段为 system_prompt、skills、skill_overrides。system_prompt 中的新增说明应使用中文；"
                    "skill_overrides 的每个值必须是完整的 SKILL.md 文本字符串，不能返回嵌套对象或数组；"
                    "没有技能内容修改时使用 {}。不得修改工具白名单、任务依赖图或平台安全规则，"
                    "skills 必须来自 available_skills。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            raw = self._call_model(messages)
        except CustomizationError as exc:
            # One bounded retry handles providers that occasionally emit an
            # empty/non-JSON response under a long prompt without retrying
            # semantic validation failures indefinitely.
            if "model returned invalid JSON" not in str(exc):
                raise
            retry_messages = [
                *messages,
                {"role": "user", "content": "上一轮未返回有效 JSON。请只返回包含 system_prompt、skills、skill_overrides 的 JSON 对象，不要解释。"},
            ]
            raw = self._call_model(retry_messages)
        if not self._has_candidate_fields(raw):
            retry_messages = [
                *messages,
                {"role": "user", "content": "上一轮返回为空。请只返回包含 system_prompt、skills、skill_overrides 的 JSON 对象，不要解释。"},
            ]
            raw = self._call_model(retry_messages)
        try:
            normalized = self._normalize_model_candidate_payload(raw, current)
            return AgentCandidate.model_validate(normalized)
        except Exception as exc:
            raise CustomizationError(f"model candidate failed validation: {exc}") from exc

    def _model_review(
        self,
        preset_name: str,
        agent_id: str,
        current: AgentCandidate,
        candidate: AgentCandidate,
        diff: dict[str, Any],
    ) -> ProposalReview:
        raw = self._call_model([
            {
                "role": "system",
                "content": (
                    "请审核金融研究智能体的候选配置修改。请用中文返回 JSON，字段为 approved（布尔值）、"
                    "risk_level（low/medium/high）、findings（含 type/message 的对象数组）、"
                    "checks（含 name/passed/message 的对象数组）。拒绝未知技能、削弱安全与数据纪律、"
                    "偏离角色职责的修改。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"preset_name": preset_name, "agent_id": agent_id, "before": current.model_dump(), "after": candidate.model_dump(), "diff": diff},
                    ensure_ascii=False,
                ),
            },
        ])
        try:
            return ProposalReview.model_validate(raw)
        except Exception as exc:
            raise CustomizationError(f"model review failed validation: {exc}") from exc

    @staticmethod
    def _diff(before: AgentCandidate, after: AgentCandidate) -> dict[str, Any]:
        prompt_diff = "\n".join(
            difflib.unified_diff(
                before.system_prompt.splitlines(),
                after.system_prompt.splitlines(),
                fromfile="current/system_prompt",
                tofile="proposal/system_prompt",
                lineterm="",
            )
        )
        before_skills = set(before.skills)
        after_skills = set(after.skills)
        changed = sorted(name for name in before_skills & after_skills if before.skill_overrides.get(name) != after.skill_overrides.get(name))
        return {
            "prompt": prompt_diff,
            "skills_added": sorted(after_skills - before_skills),
            "skills_removed": sorted(before_skills - after_skills),
            "skills_modified": changed,
        }

    def revise_proposal(
        self,
        proposal_id: str,
        base_revision: str,
        candidate: AgentCandidate,
    ) -> AgentEditProposal:
        """Re-review a candidate edited by the user before it can be applied."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise FileNotFoundError(f"proposal {proposal_id} not found")
        current = self.effective_candidate(proposal.preset_name, proposal.agent_id)
        actual_revision = self.revision_for(current)
        if base_revision != actual_revision or proposal.base_revision != actual_revision:
            raise RevisionConflict("agent configuration changed; reload before revising")
        self._validate_candidate(candidate)
        diff = self._diff(current, candidate)
        review = self._model_review(proposal.preset_name, proposal.agent_id, current, candidate, diff)
        revised = proposal.model_copy(update={"candidate": candidate, "diff": diff, "review": review})
        with self._lock:
            self._proposals[proposal_id] = revised
            if proposal.session_id:
                self._proposals[proposal.session_id] = revised
        return revised

    def apply(self, proposal_id: str, base_revision: str) -> dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise FileNotFoundError(f"proposal {proposal_id} not found")
        if not proposal.review.approved:
            raise CustomizationError("proposal has not passed review")
        current = self.effective_candidate(proposal.preset_name, proposal.agent_id)
        actual_revision = self.revision_for(current)
        if base_revision != actual_revision or proposal.base_revision != actual_revision:
            raise RevisionConflict("agent configuration changed; reload before applying")
        revision = self._write_override(proposal, proposal.candidate)
        self.refresh()
        return {**self.editor_payload(proposal.preset_name, proposal.agent_id), "revision": revision, "reloaded": True}

    def proposal(self, proposal_id: str) -> AgentEditProposal | None:
        """Return a pending proposal without exposing the proposal store."""
        return self._proposals.get(proposal_id)

    def reset(self, preset_name: str, agent_id: str, base_revision: str) -> dict[str, Any]:
        current = self.effective_candidate(preset_name, agent_id)
        if base_revision != self.revision_for(current):
            raise RevisionConflict("agent configuration changed; reload before resetting")
        path = self._override_path(preset_name, agent_id)
        history = self._history_path(preset_name, agent_id)
        if path.exists():
            with self._lock:
                history.parent.mkdir(parents=True, exist_ok=True)
                with history.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"action": "reset", "previous_revision": base_revision, "at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")
                path.unlink()
        self.refresh()
        return {**self.editor_payload(preset_name, agent_id), "reset": True, "reloaded": True}

    def history(self, preset_name: str, agent_id: str) -> list[dict[str, Any]]:
        path = self._history_path(preset_name, agent_id)
        if not path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                entries.append(item)
        return entries[-50:]

    def reload(self, preset_name: str | None = None) -> dict[str, Any]:
        self.refresh()
        if preset_name:
            from src.swarm.presets import inspect_preset

            detail = inspect_preset(preset_name)
            return {"preset_name": preset_name, "valid": detail["valid"], "errors": detail["errors"], "warnings": detail["warnings"], "loaded_at": datetime.now(timezone.utc).isoformat(), "affects": "new_runs_only"}
        return {"valid": True, "errors": [], "warnings": [], "loaded_at": datetime.now(timezone.utc).isoformat(), "affects": "new_runs_only"}

    def refresh(self) -> None:
        with self._lock:
            self._cache.clear()

    # ------------------------------------------------------------------
    # Validation and persistence
    # ------------------------------------------------------------------

    def _validate_candidate(self, candidate: AgentCandidate) -> None:
        lowered_prompt = candidate.system_prompt.casefold()
        unsafe = next((marker for marker in UNSAFE_PROMPT_MARKERS if marker in lowered_prompt), None)
        if unsafe:
            raise CustomizationError(f"system_prompt contains a prohibited safety instruction: {unsafe}")
        available = {item["name"] for item in self.list_skills()}
        unknown = sorted(set(candidate.skills) - available)
        if unknown:
            raise CustomizationError("unknown skills: " + ", ".join(unknown))
        invalid_overrides = sorted(set(candidate.skill_overrides) - set(candidate.skills))
        if invalid_overrides:
            raise CustomizationError("skill overrides must belong to skills: " + ", ".join(invalid_overrides))

    def _write_override(self, proposal: AgentEditProposal, candidate: AgentCandidate) -> str:
        self._validate_candidate(candidate)
        revision = self.revision_for(candidate)
        now = datetime.now(timezone.utc).isoformat()
        payload = AgentOverride(
            preset_name=proposal.preset_name,
            agent_id=proposal.agent_id,
            revision=revision,
            updated_at=now,
            **candidate.model_dump(),
        )
        path = self._override_path(proposal.preset_name, proposal.agent_id)
        history = self._history_path(proposal.preset_name, proposal.agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        with history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"action": "apply", "proposal_id": proposal.proposal_id, "instruction": proposal.instruction, "revision": revision, "at": now, "candidate": candidate.model_dump(), "changed": proposal.diff}, ensure_ascii=False) + "\n")
        return revision


_default_service = AgentCustomizationService()


def get_customization_service() -> AgentCustomizationService:
    return _default_service


def apply_agent_overrides(preset_name: str, data: dict[str, Any]) -> dict[str, Any]:
    return _default_service.apply_overrides_to_preset_data(preset_name, data)
