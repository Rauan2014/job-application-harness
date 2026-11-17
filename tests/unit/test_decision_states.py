"""Testes unitários para a montagem de estados (decision/states)."""

from core.decision.states import (
    build_dp1_job_fit_state,
    build_dp2_resume_fallback_state,
    build_dp3_blocker_state,
    build_dp4_sensitivity_state,
    build_dp5_field_mapping_state,
    build_dp6_ready_to_submit_state,
    clean_option_label,
)
from core.models import FormQuestion, Job, PageInfo, Profile


def test_no_pii_in_any_state() -> None:
    """Verifica de forma estrita que NENHUM dado de PII (nome, email, telefone) aparece nos estados."""
    profile = Profile(
        full_name="Carlos Eduardo Silveira",
        email="carlos.eduardo@segredo.com.br",
        phone="+5511987654321",
        location="Rua das Flores, 123, São Paulo",
        skills=["Python", "FastAPI"],
        headline="AI Software Engineer",
        eeo={"gender": "Masculino"},
    )
    job = Job(
        id="1", row_index=1, company="Tech Corp", title="Senior AI Dev",
        stack=["Python", "FastAPI"], description="Vaga confidencial."
    )
    page_info = PageInfo(state_summary="Application form loaded with 5 inputs.")
    question = FormQuestion(id="q1", label="LinkedIn Profile", required=True)

    states = [
        build_dp1_job_fit_state(job, profile),
        build_dp2_resume_fallback_state(job, "Long job description"),
        build_dp3_blocker_state(page_info),
        build_dp4_sensitivity_state(question),
        build_dp5_field_mapping_state(question),
        build_dp6_ready_to_submit_state(
            [question],
            filled={"q1": "https://linkedin.com/in/carlos-eduardo-secret"},
            visible_errors=[],
        ),
    ]

    pii_terms = [
        "Carlos Eduardo Silveira",
        "carlos.eduardo@segredo.com.br",
        "+5511987654321",
        "987654321",
        "Rua das Flores",
        "carlos-eduardo-secret",
    ]

    for st in states:
        serialized = " ".join(str(v) for v in st.values())
        for pii in pii_terms:
            assert pii not in serialized


def test_order_of_keys_most_important_first() -> None:
    job = Job(id="1", row_index=1, company="CI&T", title="AI Engineer", stack=["Python"])
    profile = Profile(
        full_name="Test", email="test@test.com", phone="123", location="SP",
        skills=["Python"], headline="Engineer"
    )
    dp1 = build_dp1_job_fit_state(job, profile)
    keys = list(dp1.keys())
    assert keys[0] == "job_title"
    assert keys[1] == "job_company"
    assert keys[-1] == "job_description"  # descrição truncada fica no fim


def test_state_max_chars_respected() -> None:
    job = Job(id="1", row_index=1, company="Corp", title="Dev")
    giant_desc = "X" * 10000
    st = build_dp2_resume_fallback_state(job, giant_desc, max_chars=1000)
    total_len = sum(len(v) for v in st.values())
    assert total_len <= 1500
    assert len(st["job_description"]) <= 1000


def test_clean_option_label() -> None:
    long_label = "Esta é uma descrição extremamente longa de opção que precisa ser encurtada"
    cleaned = clean_option_label(long_label)
    assert len(cleaned) <= 40
    assert clean_option_label("  Espaços   duplos  ") == "Espaços duplos"
