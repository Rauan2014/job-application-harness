"""Modelos e tipos para a camada de decisão calibrada (Laya)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Band = Literal["no", "uncertain", "yes"]


class NoulVerdict(BaseModel):
    """Veredicto recalculado localmente para pergunta sim/não."""

    model_config = ConfigDict(extra="allow")

    p_true: float
    band: Band
    server_band: Band | None = None
    truncated: bool = False


class ChoiceVerdict(BaseModel):
    """Veredicto para pergunta de múltipla escolha com mitigação de ordem."""

    model_config = ConfigDict(extra="allow")

    probs: dict[str, float]
    top: str
    margin: float
    order_consistent: bool = True
    truncated: bool = False


class HealthInfo(BaseModel):
    """Diagnóstico do sidecar reportado em /health."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    demoted_to_cpu: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PlanInfo(BaseModel):
    """Resultado do pré-voo em /plan sem forward pass."""

    model_config = ConfigDict(extra="allow")

    truncated_state: bool = False
    truncated_options: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


DecisionSource = Literal["laya", "rules", "llm", "sheet", "fallback"]


class DecisionRecord(BaseModel):
    """Registro de auditoria para artifacts/decisions.jsonl (sem PII, hash do estado)."""

    dp: str
    job_id: str
    question_id: str | None = None
    kind: str
    mode: Literal["shadow", "active"]
    probs: dict[str, float] | None = None
    band: Band | None = None
    laya_action: str | None = None
    applied_action: str
    source: DecisionSource
    latency_ms: float = 0.0
    state_sha256: str
    ts: str
