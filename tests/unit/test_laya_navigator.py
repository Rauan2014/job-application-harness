"""Testes unitários do LayaWebNavigator, LayaGenericHandler e roteamento no Pipeline/CLI."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import main as cli_main
from automation.laya_navigator import (
    LayaGenericHandler,
    LayaWebNavigator,
    SemanticSnapshot,
    WebElementNode,
)
from automation.registry import reset_registry
from config.settings import Settings
from core.decision.layer import DecisionLayer
from core.decision.models import ChoiceVerdict, NoulVerdict
from core.matcher import Matcher
from core.models import (
    ApplicationResult,
    DecisionUnavailable,
    FormQuestion,
    Job,
    PageInfo,
    Profile,
    ResumeInfo,
    TaskStatus,
)
from core.pipeline import Pipeline


def _make_profile() -> Profile:
    return Profile(
        full_name="Ana Silva",
        email="ana.silva@example.com",
        phone="+55 11 99999-0000",
        location="São Paulo, SP",
        links={
            "linkedin": "https://linkedin.com/in/anasilva",
            "github": "https://github.com/anasilva",
        },
        work_authorization="Sim, cidadã brasileira",
        salary_expectation="R$ 15.000",
        skills=["Python", "Playwright", "FastAPI"],
        headline="Engenheira de Software Sênior",
        current_company="TechCorp",
        education_school="USP",
        eeo={"race": "Prefer not to say", "gender": "Prefer not to say"},
    )


def _make_job(url: str = "https://portal-desconhecido.example.com/vaga/123") -> Job:
    return Job(
        id="job_nav_1",
        row_index=2,
        company="Portal Desconhecido",
        title="Engenheira Python",
        url=url,
        ats="custom_portal",
        fit_score=90,
        language="pt",
    )


class _StubModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.choice_fn: Callable[..., Any] | None = None
        self.noul_fn: Callable[..., Any] | None = None

    async def noul(self, state, instructions, criteria=None):
        self.calls.append({"type": "noul", "state": state, "instructions": instructions})
        if self.noul_fn is not None:
            return self.noul_fn(state, instructions)
        norm = instructions.lower()
        if "bloquead" in norm or "captcha" in norm or "sensíve" in norm or "sensitive" in norm:
            return NoulVerdict(p_true=0.05, band="no")
        return NoulVerdict(p_true=0.9, band="yes")

    async def choice(self, state, instructions, options):
        self.calls.append({"type": "choice", "state": state, "instructions": instructions, "options": options})
        if self.choice_fn is not None:
            return self.choice_fn(state, instructions, options)
        top = next(iter(options))
        return ChoiceVerdict(
            probs={k: (0.9 if k == top else 0.01) for k in options},
            top=top,
            margin=0.89,
            order_consistent=True,
        )


class _FakeMatcher(Matcher):
    def __init__(self, settings: Settings, picked: str = "none") -> None:
        super().__init__(settings=settings)
        self.picked = picked
        self.ask_calls: list[list[dict[str, str]]] = []

    async def _ask_json(self, messages, parse):
        self.ask_calls.append(messages)
        return parse(json.loads(json.dumps({"choice": self.picked})))


@pytest.mark.asyncio
async def test_banking77_option_budget_cap_enforced(test_settings: Settings) -> None:
    model = _StubModel()
    nav = LayaWebNavigator(decision_model=model, max_options=8)

    many_buttons = [
        WebElementNode(
            eid=f"e{i}",
            tag="button",
            label=f"Candidatar-se na vaga especialidade #{i}",
        )
        for i in range(25)
    ]
    snap = SemanticSnapshot(
        url="https://unknown.example.com/jobs",
        title="Carreiras",
        elements=many_buttons,
    )

    chosen, by = await nav.choose_navigation_action(snap, goal="open_form")
    assert chosen is not None
    assert by == "laya"
    choice_call = next(c for c in model.calls if c["type"] == "choice")
    assert len(choice_call["options"]) <= 8
    assert "none" in choice_call["options"]
    assert all(len(v) <= 38 for v in choice_call["options"].values())


@pytest.mark.asyncio
async def test_system1_confident_choice_skips_llm(test_settings: Settings) -> None:
    model = _StubModel()
    model.choice_fn = lambda s, i, opts: ChoiceVerdict(
        probs={k: (0.85 if k == "e1" else 0.05) for k in opts},
        top="e1",
        margin=0.80,
        order_consistent=True,
    )
    matcher = _FakeMatcher(test_settings, picked="e0")
    nav = LayaWebNavigator(decision_model=model, matcher=matcher, min_margin=0.12)

    snap = SemanticSnapshot(
        url="https://unknown.example.com/vaga",
        title="Engenheira Python",
        elements=[
            WebElementNode(eid="e0", tag="a", label="Candidatar depois"),
            WebElementNode(eid="e1", tag="button", label="Candidatar-se agora"),
        ],
    )

    chosen, by = await nav.choose_navigation_action(snap, goal="open_form")
    assert chosen is not None and chosen.eid == "e1"
    assert by == "laya"
    assert nav.stats.laya_decisions == 1
    assert nav.stats.llm_escalations == 0
    assert len(matcher.ask_calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("margin", "order_consistent", "truncated"),
    [
        (0.03, True, False),
        (0.40, False, False),
        (0.50, True, True),
    ],
)
async def test_system2_escalation_on_uncertain_verdict(
    test_settings: Settings,
    margin: float,
    order_consistent: bool,
    truncated: bool,
) -> None:
    test_settings.NO_LLM = False
    model = _StubModel()
    model.choice_fn = lambda s, i, opts: ChoiceVerdict(
        probs={k: 0.5 / len(opts) for k in opts},
        top="e0",
        margin=margin,
        order_consistent=order_consistent,
        truncated=truncated,
    )
    matcher = _FakeMatcher(test_settings, picked="e1")
    nav = LayaWebNavigator(decision_model=model, matcher=matcher, min_margin=0.12)

    snap = SemanticSnapshot(
        url="https://unknown.example.com/vaga",
        title="Vaga",
        elements=[
            WebElementNode(eid="e0", tag="button", label="Avançar para revisar"),
            WebElementNode(eid="e1", tag="button", label="Iniciar candidatura"),
        ],
    )

    chosen, by = await nav.choose_navigation_action(snap, goal="open_form")
    assert chosen is not None and chosen.eid == "e1"
    assert by == "llm"
    assert nav.stats.llm_escalations == 1
    assert len(matcher.ask_calls) == 1


@pytest.mark.asyncio
async def test_offline_laya_degrades_gracefully_to_rules(test_settings: Settings) -> None:
    model = _StubModel()

    def _boom(*args, **kwargs):
        raise DecisionUnavailable("sidecar offline")

    model.choice_fn = _boom
    model.noul_fn = _boom
    nav = LayaWebNavigator(decision_model=model, matcher=None)

    snap = SemanticSnapshot(
        url="https://unknown.example.com/vaga",
        title="Vaga",
        elements=[
            WebElementNode(eid="e0", tag="a", label="Saiba mais sobre a empresa"),
            WebElementNode(eid="e1", tag="button", label="Candidatar-se para esta vaga"),
        ],
    )

    chosen, by = await nav.choose_navigation_action(snap, goal="open_form")
    assert chosen is not None and chosen.eid == "e1"
    assert by == "rule"
    assert nav.stats.rule_fallbacks >= 1


@pytest.mark.asyncio
async def test_eeo_shield_never_invokes_laya_or_llm(test_settings: Settings) -> None:
    test_settings.NO_LLM = False
    model = _StubModel()
    matcher = _FakeMatcher(test_settings, picked="full_name")
    nav = LayaWebNavigator(decision_model=model, matcher=matcher)
    profile = _make_profile()

    q_sens = FormQuestion(
        id="q_eeo",
        label="Raça / Etnia (opcional)",
        kind="select",
        options=["Branca", "Preta", "Prefer not to say"],
    )

    key, by = await nav.map_field_to_profile_key(q_sens)
    opt = await nav.resolve_select_option(q_sens.label, q_sens.options, None, profile)

    assert key == "eeo_sensitive"
    assert by == "rule"
    assert opt is None
    assert len(model.calls) == 0
    assert len(matcher.ask_calls) == 0


@pytest.mark.asyncio
async def test_laya_resolves_obfuscated_profile_field_before_llm(test_settings: Settings) -> None:
    model = _StubModel()

    def _pick_full_name(state, instructions, options):
        lbl = state.get("label", "").lower()
        if "chamar" in lbl and "full_name" in options:
            target = "full_name"
        else:
            target = "custom_question" if "custom_question" in options else next(iter(options))
        return ChoiceVerdict(
            probs={k: (0.9 if k == target else 0.02) for k in options},
            top=target,
            margin=0.88,
            order_consistent=True,
        )

    model.choice_fn = _pick_full_name
    matcher = _FakeMatcher(test_settings)
    nav = LayaWebNavigator(decision_model=model, matcher=matcher)
    profile = _make_profile()

    questions = [
        FormQuestion(id="x_name", label="Como devemos te chamar?", kind="text", required=True),
        FormQuestion(id="x_open", label="Conte sobre sua arquitetura favorita", kind="textarea", required=False),
    ]
    resolved = await nav.resolve_profile_fields_with_laya(questions, profile)

    assert resolved["x_name"] == "Ana Silva"
    assert "x_open" not in resolved
    assert len(matcher.ask_calls) == 0


@pytest.mark.asyncio
async def test_pipeline_skip_vs_laya_navigator_flag(
    test_settings: Settings,
    sample_resume_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_registry()
    profile = _make_profile()
    job = _make_job("https://desconhecido.example.org/carreiras/99")
    resume_info = ResumeInfo(path=sample_resume_pdf, summary="Python Sênior", text="Ana Silva Python FastAPI")

    class _FakeResolver:
        async def resolve(self, _job):
            return resume_info

        def get_base_resumes(self, _used):
            return []

        def get_resumes(self):
            return {"default": resume_info}

    class _FakeBrowserManager:
        @asynccontextmanager
        async def new_page(self):
            yield object()

    test_settings.AUTONOMOUS_MODE = False
    test_settings.LAYA_WEB_NAVIGATOR_ENABLED = False
    decision_layer = DecisionLayer(model=_StubModel(), settings=test_settings)
    matcher = Matcher(settings=test_settings)

    pipeline_off = Pipeline(
        settings=test_settings,
        decision_layer=decision_layer,
        resume_resolver=_FakeResolver(),  # type: ignore[arg-type]
        browser_manager=_FakeBrowserManager(),  # type: ignore[arg-type]
        matcher=matcher,
    )
    res_off = await pipeline_off.process_job(job, profile)
    assert res_off.status == TaskStatus.SKIPPED

    # Agora com LAYA_WEB_NAVIGATOR_ENABLED=True: aciona LayaGenericHandler
    test_settings.LAYA_WEB_NAVIGATOR_ENABLED = True
    called_handler: list[str] = []

    async def _fake_inspect(self, page, _job):
        called_handler.append(self.name)
        return PageInfo(description="Vaga Python", questions=[], state_summary="ok")

    async def _fake_fill(self, page, job, profile, resume, answers, dry_run=True, ready_check=None):
        return ApplicationResult(
            job_id=job.id,
            status=TaskStatus.COMPLETED,
            dry_run=dry_run,
            submitted=False,
            resume_used=resume.path.name,
        )

    monkeypatch.setattr(LayaGenericHandler, "inspect", _fake_inspect)
    monkeypatch.setattr(LayaGenericHandler, "fill_and_submit", _fake_fill)
    monkeypatch.setattr(LayaGenericHandler, "_check_blockers", AsyncMock(return_value=None))

    pipeline_on = Pipeline(
        settings=test_settings,
        decision_layer=decision_layer,
        resume_resolver=_FakeResolver(),  # type: ignore[arg-type]
        browser_manager=_FakeBrowserManager(),  # type: ignore[arg-type]
        matcher=matcher,
    )
    res_on = await pipeline_on.process_job(job, profile)
    assert res_on.status == TaskStatus.COMPLETED
    assert called_handler == ["laya_generic"]


def test_cli_laya_navigator_flags() -> None:
    args_on = cli_main.parse_args(["--laya-navigator", "--dry-run"])
    assert args_on.laya_navigator is True

    args_off = cli_main.parse_args(["--no-laya-navigator"])
    assert args_off.laya_navigator is False

    args_default = cli_main.parse_args([])
    assert args_default.laya_navigator is None
