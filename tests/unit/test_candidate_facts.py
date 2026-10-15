"""Testes unitários para validação de respostas e fatos do candidato (Laya / Regras Determinísticas)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.decision.rules import match_option, resolve_profile_answer
from core.models import FormQuestion, Profile


@pytest.fixture
def real_profile() -> Profile:
    data = json.loads(Path("data/profile.json").read_text(encoding="utf-8"))
    return Profile.model_validate(data)


def test_visa_and_sponsorship_answers(real_profile: Profile) -> None:
    # 1. Visa Sponsorship -> Não / No
    q_sponsor_en = FormQuestion(
        id="q1", label="Do you require visa sponsorship?", kind="select", options=["Yes", "No"]
    )
    assert resolve_profile_answer(q_sponsor_en, real_profile) == "No"

    q_sponsor_pt = FormQuestion(
        id="q2", label="Você precisa de patrocínio de visto?", kind="select", options=["Sim", "Não"]
    )
    assert resolve_profile_answer(q_sponsor_pt, real_profile) == "Não"

    # 2. Legal Work Authorization in Brazil -> Sim / Yes
    q_auth_en = FormQuestion(
        id="q3", label="Are you legally authorized to work in Brazil?", kind="select", options=["Yes", "No"]
    )
    assert resolve_profile_answer(q_auth_en, real_profile) == "Yes"

    q_auth_pt = FormQuestion(
        id="q4",
        label="Possui autorização legal para trabalhar no país de residência (Brasil)?",
        kind="select",
        options=["Sim", "Não"],
    )
    assert resolve_profile_answer(q_auth_pt, real_profile) == "Sim"


def test_years_of_experience_specifics(real_profile: Profile) -> None:
    # TypeScript / JavaScript -> 3 anos
    q_ts = FormQuestion(id="q_ts", label="Quantos anos de experiência você tem com TypeScript?")
    assert resolve_profile_answer(q_ts, real_profile) == "3 anos"

    q_js = FormQuestion(id="q_js", label="How many years of experience with JavaScript?")
    assert resolve_profile_answer(q_js, real_profile) in ("3", "3 anos")

    # React / Next.js -> 3 anos (ou opção 2 a 3 anos em select)
    q_react_select = FormQuestion(
        id="q_react",
        label="Experiência com React e Next.js:",
        kind="select",
        options=["Menos de 1 ano", "1 a 2 anos", "2 a 3 anos", "Mais de 5 anos"],
    )
    assert resolve_profile_answer(q_react_select, real_profile) == "2 a 3 anos"

    # PostgreSQL / SQL -> 3 anos
    q_sql = FormQuestion(id="q_sql", label="Anos de experiência com PostgreSQL e bancos de dados SQL?")
    assert resolve_profile_answer(q_sql, real_profile) == "3 anos"

    # Engenharia de IA / LLMs -> 2 anos
    q_ai = FormQuestion(id="q_ai", label="Quantos anos de experiência com IA Generativa e LLMs?")
    assert resolve_profile_answer(q_ai, real_profile) == "2 anos"

    # Tempo total como desenvolvedor -> 3 anos
    q_total = FormQuestion(id="q_total", label="Tempo total de experiência como desenvolvedor de software?")
    assert resolve_profile_answer(q_total, real_profile) == "3 anos"


def test_education_answers(real_profile: Profile) -> None:
    # Faculdade / Instituição
    q_school = FormQuestion(id="q_school", label="Qual a instituição de ensino / faculdade?")
    assert "UNICSUL" in (resolve_profile_answer(q_school, real_profile) or "")

    # Curso
    q_degree = FormQuestion(id="q_degree", label="Qual o seu curso de graduação?")
    assert "Análise e Desenvolvimento de Sistemas" in (resolve_profile_answer(q_degree, real_profile) or "")

    # Grau / Escolaridade
    q_level = FormQuestion(
        id="q_level",
        label="Nível de escolaridade:",
        kind="select",
        options=["Ensino Médio", "Ensino Superior Completo", "Pós-graduação"],
    )
    assert resolve_profile_answer(q_level, real_profile) == "Ensino Superior Completo"

    # Ano de conclusão
    q_year = FormQuestion(id="q_year", label="Ano de conclusão do curso?")
    assert resolve_profile_answer(q_year, real_profile) == "2024"


def test_contract_cnpj_and_travel(real_profile: Profile) -> None:
    # Regime de contratação
    q_contract = FormQuestion(id="q_contract", label="Regime de contratação de preferência?")
    assert "PJ" in (resolve_profile_answer(q_contract, real_profile) or "")

    # Possui CNPJ ativo
    q_cnpj = FormQuestion(id="q_cnpj", label="Possui CNPJ ativo para emissão de NF?")
    assert resolve_profile_answer(q_cnpj, real_profile) in ("Sim", "Sim / Yes")

    # Viagens
    q_travel = FormQuestion(id="q_travel", label="Disponibilidade para viajar para encontros da equipe?")
    assert resolve_profile_answer(q_travel, real_profile) in ("Sim", "Yes", "Yes / Sim", "Sim / Yes")

    q_travel_opt = FormQuestion(
        id="q_travel_opt",
        label="Disponibilidade para viajar?",
        kind="select",
        options=["Sim", "Não"],
    )
    assert resolve_profile_answer(q_travel_opt, real_profile) == "Sim"



def test_compliance_and_conflict_answers(real_profile: Profile) -> None:
    # Já trabalhou na empresa -> Não / No
    q_worked = FormQuestion(
        id="q_worked",
        label="Já trabalhou anteriormente para alguma das nossas empresas?",
        kind="select",
        options=["Sim", "Não"],
    )
    assert resolve_profile_answer(q_worked, real_profile) == "Não"

    # Parentes ou amigos -> Não / No
    q_relatives = FormQuestion(
        id="q_relatives",
        label="Do you have any relatives or friends working at the company?",
        kind="select",
        options=["Yes", "No"],
    )
    assert resolve_profile_answer(q_relatives, real_profile) == "No"

    # Non-compete -> Não / No
    q_noncompete = FormQuestion(
        id="q_nc",
        label="Possui acordo de não-competição (Non-Compete) ativo?",
        kind="select",
        options=["Sim", "Não"],
    )
    assert resolve_profile_answer(q_noncompete, real_profile) == "Não"

    # Maior de 18 anos -> Sim / Yes
    q_age = FormQuestion(
        id="q_age", label="Você é maior de 18 anos de idade?", kind="select", options=["Sim", "Não"]
    )
    assert resolve_profile_answer(q_age, real_profile) == "Sim"


def test_bilingual_cover_letters(real_profile: Profile) -> None:
    # Pergunta em português
    q_cl_pt = FormQuestion(
        id="q_cl_pt", label="Carta de apresentação ou mensagem para a equipe:", kind="textarea"
    )
    cl_pt = resolve_profile_answer(q_cl_pt, real_profile)
    assert cl_pt is not None
    assert "Sou AI Engineer e Desenvolvedor Full-Stack" in cl_pt

    # Pergunta em inglês
    q_cl_en = FormQuestion(
        id="q_cl_en", label="Please provide your cover letter or introductory note:", kind="textarea"
    )
    cl_en = resolve_profile_answer(q_cl_en, real_profile)
    assert cl_en is not None
    assert "I am an AI Engineer and Full-Stack Developer" in cl_en


def test_match_option_helper() -> None:
    assert match_option("Sim / Yes", ["No", "Yes"]) == "Yes"
    assert match_option("Não / No", ["Sim", "Não"]) == "Não"
    assert match_option("3 anos", ["Menos de 1 ano", "1 a 2 anos", "3 a 5 anos", "5+ anos"]) == "3 a 5 anos"
    assert match_option("2024", ["2022", "2023", "2024", "2025"]) == "2024"
