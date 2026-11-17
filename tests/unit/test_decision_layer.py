"""Testes unitários para a camada de decisão (DecisionLayer)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config.settings import Settings
from core.decision.layer import DecisionAudit, DecisionLayer
from core.decision.models import ChoiceVerdict, NoulVerdict
from core.models import DecisionUnavailable, FormQuestion, Job, PageInfo, Profile, ResumeChoice, ResumeInfo
from tests.conftest import FakeDecisionModel


@pytest.fixture
def mock_audit(tmp_path: Path) -> DecisionAudit:
    return DecisionAudit(tmp_path / "decisions.jsonl")


@pytest.fixture
def dummy_profile() -> Profile:
    return Profile(
        full_name="User Test",
        email="test@test.com",
        phone="123",
        location="SP",
        skills=["Python", "FastAPI"],
        headline="AI Dev",
        base_answers={"salary_expectation": "R$ 15.000"},
    )


# -----------------------------------------------------------------------------
# Testes de Rebaixamento de Modos (Demotion)
# -----------------------------------------------------------------------------
def test_demotion_dp1_dp2_dp5_without_approved_eval(test_settings: Settings, mock_audit: DecisionAudit) -> None:
    # Configura DP1, DP2 e DP5 como active
    cfg = test_settings.model_copy(update={
        "LAYA_DP_MODES": {
            "DP1": "active", "DP2": "active", "DP3": "active",
            "DP4": "active", "DP5": "active", "DP6": "active",
        },
        "LAYA_REQUIRE_EVAL_FOR_ACTIVE": True,
    })

    # 1. Sem avaliação -> rebaixa DP1, DP2, DP5 para shadow; DP3, DP4, DP6 permanecem active
    layer_no_eval = DecisionLayer(None, cfg, audit=mock_audit, eval_report=None)
    assert layer_no_eval.effective_modes["DP1"] == "shadow"
    assert layer_no_eval.effective_modes["DP2"] == "shadow"
    assert layer_no_eval.effective_modes["DP5"] == "shadow"
    assert layer_no_eval.effective_modes["DP3"] == "active"
    assert layer_no_eval.effective_modes["DP4"] == "active"
    assert layer_no_eval.effective_modes["DP6"] == "active"

    # 2. Avaliação vencida (> 14 dias) -> rebaixa
    old_ts = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    eval_old = {
        "DP1": {"passed": True, "ts": old_ts},
        "DP2": {"passed": True, "ts": old_ts},
        "DP5": {"passed": True, "ts": old_ts},
    }
    layer_old_eval = DecisionLayer(None, cfg, audit=mock_audit, eval_report=eval_old)
    assert layer_old_eval.effective_modes["DP1"] == "shadow"

    # 3. Avaliação reprovada (passed=False) -> rebaixa
    fresh_ts = datetime.now(UTC).isoformat()
    eval_failed = {"DP1": {"passed": False, "ts": fresh_ts}}
    layer_failed_eval = DecisionLayer(None, cfg, audit=mock_audit, eval_report=eval_failed)
    assert layer_failed_eval.effective_modes["DP1"] == "shadow"

    # 4. Avaliação aprovada e recente -> liberados em active
    eval_ok = {
        "DP1": {"passed": True, "ts": fresh_ts},
        "DP2": {"passed": True, "ts": fresh_ts},
        "DP5": {"passed": True, "ts": fresh_ts},
    }
    layer_ok_eval = DecisionLayer(None, cfg, audit=mock_audit, eval_report=eval_ok)
    assert layer_ok_eval.effective_modes["DP1"] == "active"
    assert layer_ok_eval.effective_modes["DP2"] == "active"
    assert layer_ok_eval.effective_modes["DP5"] == "active"


# -----------------------------------------------------------------------------
# Testes DP1 (job_fit)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dp1_job_fit_rules_and_active(
    test_settings: Settings, mock_audit: DecisionAudit, dummy_profile: Profile
) -> None:
    fresh_ts = datetime.now(UTC).isoformat()
    eval_ok = {"DP1": {"passed": True, "ts": fresh_ts}}
    cfg = test_settings.model_copy(update={
        "LAYA_DP_MODES": {"DP1": "active"},
        "MIN_FIT_SCORE": 70,
    })

    fake_model = FakeDecisionModel()
    layer = DecisionLayer(fake_model, cfg, audit=mock_audit, eval_report=eval_ok)

    # 1. fit_score da planilha < MIN_FIT_SCORE -> sempre SKIPPED, mesmo que Laya diga YES
    fake_model.noul_verdicts = [NoulVerdict(p_true=0.95, band="yes")]
    job_low = Job(id="j1", row_index=1, company="A", title="Dev", fit_score=60)
    action, reason = await layer.decide_job_fit(job_low, dummy_profile)
    assert action == "SKIPPED"
    assert "abaixo do mínimo" in str(reason)

    # 2. fit_score da planilha >= MIN_FIT_SCORE mas Laya diz NO -> divergência gera NEEDS_REVIEW, nunca SKIPPED
    fake_model.noul_verdicts = [NoulVerdict(p_true=0.10, band="no")]
    job_high = Job(id="j2", row_index=2, company="A", title="Dev", fit_score=85)
    action, reason = await layer.decide_job_fit(job_high, dummy_profile)
    assert action == "NEEDS_REVIEW"
    assert "Divergência" in str(reason)

    # 3. fit_score é None e Laya diz NO -> SKIPPED
    fake_model.noul_verdicts = [NoulVerdict(p_true=0.10, band="no")]
    job_none_fit = Job(id="j3", row_index=3, company="A", title="Dev", fit_score=None)
    action, reason = await layer.decide_job_fit(job_none_fit, dummy_profile)
    assert action == "SKIPPED"

    # 4. fit_score é None e Laya diz UNCERTAIN -> NEEDS_REVIEW
    fake_model.noul_verdicts = [NoulVerdict(p_true=0.50, band="uncertain")]
    action, reason = await layer.decide_job_fit(job_none_fit, dummy_profile)
    assert action == "NEEDS_REVIEW"


@pytest.mark.asyncio
async def test_dp1_fit_gate_disabled_allows_low_fit_score(
    test_settings: Settings, mock_audit: DecisionAudit, dummy_profile: Profile
) -> None:
    """REF-7 / QA-4: Quando FIT_GATE_ENABLED=False, fit_score < MIN_FIT_SCORE não é cortado deterministamente."""
    cfg = test_settings.model_copy(update={
        "LAYA_DP_MODES": {"DP1": "off"},
        "FIT_GATE_ENABLED": False,
        "MIN_FIT_SCORE": 70,
    })
    layer = DecisionLayer(None, cfg, audit=mock_audit)
    job_low = Job(id="j_disabled", row_index=1, company="A", title="Dev", fit_score=40)
    action, reason = await layer.decide_job_fit(job_low, dummy_profile)
    assert action == "PROCEED"


# -----------------------------------------------------------------------------
# Testes DP2 (resume_fallback)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dp2_resume_fallback_active_and_shadow(
    test_settings: Settings, mock_audit: DecisionAudit, tmp_path: Path
) -> None:
    res_a = ResumeInfo(path=tmp_path / "Cognition.pdf", summary="Summary A", text="A")
    res_b = ResumeInfo(path=tmp_path / "Base Inglês.pdf", summary="Summary B", text="B")
    bases = {"Cognition": res_a, "Base Inglês": res_b}
    job = Job(id="j1", row_index=1, company="A", title="Dev")

    fresh_ts = datetime.now(UTC).isoformat()
    eval_ok = {"DP2": {"passed": True, "ts": fresh_ts}}
    cfg_active = test_settings.model_copy(update={"LAYA_DP_MODES": {"DP2": "active"}})

    fake_model = FakeDecisionModel()
    layer_active = DecisionLayer(fake_model, cfg_active, audit=mock_audit, eval_report=eval_ok)

    # Em active com margin alta -> usa decisão do Laya sem chamar LLM
    fake_model.choice_verdicts = [
        ChoiceVerdict(probs={"Cognition": 0.8, "Base Inglês": 0.2}, top="Cognition", margin=0.6, order_consistent=True)
    ]
    llm_called = False
    async def fake_llm() -> ResumeChoice:
        nonlocal llm_called
        llm_called = True
        return ResumeChoice(filename="Base Inglês")

    chosen, _ = await layer_active.decide_resume_fallback(job, "Desc", bases, fake_llm)
    assert chosen == res_a
    assert llm_called is False


# -----------------------------------------------------------------------------
# Testes DP3 (blocker_check) & Assimetria de Segurança
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dp3_blocker_security_invariants(test_settings: Settings, mock_audit: DecisionAudit) -> None:
    fake_model = FakeDecisionModel()
    layer = DecisionLayer(fake_model, test_settings, audit=mock_audit)

    # Assimetria: Se a regra detectou blocker, Laya "no" NUNCA libera
    fake_model.noul_verdicts = [NoulVerdict(p_true=0.01, band="no")]
    page_with_captcha = PageInfo(
        state_summary="Contains recaptcha iframe and puzzle",
        page_signals="title: Verification | body: contains recaptcha iframe and puzzle",
    )
    is_blocked, reason = await layer.decide_blocker("job-1", page_with_captcha, dry_run=False)
    assert is_blocked is True
    assert "deterministamente" in str(reason)

    # Sem regra, mas Laya diz YES -> bloqueado
    fake_model.noul_verdicts = [NoulVerdict(p_true=0.90, band="yes")]
    page_clean = PageInfo(
        state_summary="Application form for Senior Engineer: personal info and resume attachment",
        page_signals="title: Application | host: jobs.lever.co",
    )
    is_blocked2, reason2 = await layer.decide_blocker("job-1", page_clean, dry_run=False)
    assert is_blocked2 is True
    assert "Laya detectou bloqueio" in str(reason2)


@pytest.mark.asyncio
async def test_dp3_cloudflare_company_does_not_block_in_off_mode(
    test_settings: Settings, mock_audit: DecisionAudit
) -> None:
    cfg = test_settings.model_copy(update={"LAYA_DP_MODES": {"DP3": "off"}})
    layer = DecisionLayer(None, cfg, audit=mock_audit)
    page_cloudflare = PageInfo(state_summary="Lever job 'SRE' em 'Cloudflare'", page_signals="")
    is_blocked, reason = await layer.decide_blocker("job-cloudflare", page_cloudflare, dry_run=True)
    assert is_blocked is False
    assert reason is None


# -----------------------------------------------------------------------------
# Testes DP4 (question_sensitivity) & Assimetria de Segurança
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dp4_sensitivity_security_invariants(test_settings: Settings, mock_audit: DecisionAudit) -> None:
    fake_model = FakeDecisionModel()
    layer = DecisionLayer(fake_model, test_settings, audit=mock_audit)

    # Assimetria: Se a regra tem palavra-chave sensível, Laya "no" NUNCA desmarca
    fake_model.noul_verdicts = [NoulVerdict(p_true=0.01, band="no")]
    q_eeo = FormQuestion(id="q1", label="Qual é a sua raça / etnia?")
    is_sens = await layer.decide_sensitivity("job-1", q_eeo)
    assert is_sens is True


# -----------------------------------------------------------------------------
# Testes DP6 (ready_to_submit) & Assimetria de Segurança
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dp6_ready_to_submit_security_invariants(test_settings: Settings, mock_audit: DecisionAudit) -> None:
    fake_model = FakeDecisionModel()
    layer = DecisionLayer(fake_model, test_settings, audit=mock_audit)

    # 1. Em dry-run, NUNCA submete
    can_submit_dry, _ = await layer.decide_ready_to_submit("j1", [], {}, [], dry_run=True)
    assert can_submit_dry is False

    # 2. Assimetria: com campo obrigatório faltando, Laya "yes" NUNCA autoriza envio
    fake_model.noul_verdicts = [NoulVerdict(p_true=0.99, band="yes")]
    questions = [FormQuestion(id="q1", label="Telefone", required=True)]
    filled = {"q1": ""}
    can_submit, reason = await layer.decide_ready_to_submit("j1", questions, filled, [], dry_run=False)
    assert can_submit is False
    assert "obrigatórios ausentes" in str(reason)


# -----------------------------------------------------------------------------
# Testes de Auditoria (state_sha256 e sem PII)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_logs_state_sha256_and_no_raw_text(
    test_settings: Settings, mock_audit: DecisionAudit, dummy_profile: Profile
) -> None:
    fake_model = FakeDecisionModel()
    layer = DecisionLayer(fake_model, test_settings, audit=mock_audit)

    job = Job(id="audit-job-1", row_index=1, company="Audit Corp", title="Engineer")
    await layer.decide_job_fit(job, dummy_profile)

    assert len(mock_audit.records) == 1
    rec = mock_audit.records[0]
    assert rec.job_id == "audit-job-1"
    assert len(rec.state_sha256) == 64  # SHA-256 válido
    assert "User Test" not in rec.model_dump_json()
    assert "test@test.com" not in rec.model_dump_json()


# -----------------------------------------------------------------------------
# Teste de Fallback com DecisionUnavailable
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unavailable_fallback(test_settings: Settings, mock_audit: DecisionAudit) -> None:
    class FailingDecisionModel:
        async def noul(self, *args, **kwargs):
            raise DecisionUnavailable("Sidecar off")
        async def choice(self, *args, **kwargs):
            raise DecisionUnavailable("Sidecar off")

    layer = DecisionLayer(FailingDecisionModel(), test_settings, audit=mock_audit)

    # DP3 com Laya inacessível: não derruba a execução, aplica regra determinística com source="fallback"
    page = PageInfo(state_summary="clean page")
    is_blocked, _ = await layer.decide_blocker("job-x", page, dry_run=True)
    assert is_blocked is False
    assert mock_audit.records[-1].source == "fallback"
