"""Testes unitários para regras determinísticas puras (decision/rules)."""

from core.decision.rules import has_blocker_marker, is_sensitive_question, missing_required, rank_profile_keys
from core.models import FormQuestion


def test_sensitive_keywords_pt_en() -> None:
    # Perguntas sensíveis EEO
    assert is_sensitive_question("Qual é o seu gênero?") is True
    assert is_sensitive_question("What is your gender identity?") is True
    assert is_sensitive_question("Raça / Etnia / Race / Ethnicity") is True
    assert is_sensitive_question("Você é uma pessoa com deficiência (PCD)?") is True
    assert is_sensitive_question("Do you have a disability?") is True
    assert is_sensitive_question("Are you a protected veteran?") is True
    assert is_sensitive_question("Qual é a sua orientação sexual?") is True
    assert is_sensitive_question("What are your preferred pronouns?") is True
    assert is_sensitive_question("Estado civil / Marital status") is True

    # Perguntas normais que NÃO devem ser marcadas como sensíveis
    assert is_sensitive_question("Link do LinkedIn") is False
    assert is_sensitive_question("LinkedIn Profile URL") is False
    assert is_sensitive_question("GitHub URL") is False
    assert is_sensitive_question("Quantos anos de experiência com Python?") is False
    assert is_sensitive_question("Pretensão salarial") is False
    assert is_sensitive_question("Disponibilidade para início imediato?") is False


def test_has_blocker_marker() -> None:
    assert has_blocker_marker({"summary": "Please solve this hCaptcha to continue"}) is True
    assert has_blocker_marker("Cloudflare Turnstile challenge detected") is True
    assert has_blocker_marker("Verifique que você é humano") is True
    assert has_blocker_marker("Sign in to apply for this job") is True

    assert has_blocker_marker({"title": "Application Form", "summary": "Senior Dev Application"}) is False
    assert has_blocker_marker("Normal job description text") is False
    assert has_blocker_marker("Este site é protegido pelo reCAPTCHA e está sujeito à Política de Privacidade") is False
    assert has_blocker_marker("This site is protected by reCAPTCHA and the Google Privacy Policy apply.") is False


def test_missing_required() -> None:
    questions = [
        FormQuestion(id="q1", label="Nome", required=True),
        FormQuestion(id="q2", label="Email", required=True),
        FormQuestion(id="q3", label="Website", required=False),
        FormQuestion(id="q4", label="Telefone", required=True),
    ]
    filled = {
        "q1": "Fulano",
        "q2": "   ",  # espaço em branco conta como vazio
        "q3": None,    # opcional não deve entrar
        # q4 ausente
    }
    missing = missing_required(questions, filled)
    assert "q2" in missing
    assert "q4" in missing
    assert "q1" not in missing
    assert "q3" not in missing


def test_rank_profile_keys() -> None:
    keys = [
        "full_name",
        "email",
        "phone",
        "location",
        "work_authorization",
        "salary_expectation",
        "skills",
        "headline",
        "extra_info_1",
        "extra_info_2",
        "extra_info_3",
    ]
    # Busca por expectativa salarial
    ranked_salary = rank_profile_keys("Qual a sua expectativa de salário?", keys)
    assert len(ranked_salary) <= 8
    assert ranked_salary[0] == "salary_expectation"

    # Busca por autorização de trabalho
    ranked_auth = rank_profile_keys("Are you legally authorized to work in the US?", keys)
    assert len(ranked_auth) <= 8
    assert ranked_auth[0] == "work_authorization"

    # Sempre retorna no máximo 8 chaves
    ranked_generic = rank_profile_keys("Something", keys, top_k=8)
    assert len(ranked_generic) <= 8

    # min_score filtra chaves de pontuação 0.0 para evitar poluição
    ranked_filtered = rank_profile_keys("Unrelated question about hobbies", keys, top_k=8, min_score=3.0)
    assert ranked_filtered == []


def test_resolve_profile_answer_bottleneck_rules() -> None:
    from core.decision.rules import resolve_profile_answer
    from core.models import Profile

    profile = Profile(
        full_name="Thiago Borges",
        email="thiago@exemplo.com",
        phone="11999999999",
        location="São Paulo, SP",
        skills=["Python", "TypeScript", "FastAPI", "React"],
        salary_expectation="R$ 18.000",
    )

    def _q(lbl: str) -> FormQuestion:
        return FormQuestion(id="q", label=lbl, required=True)

    # Follow-up condicional
    assert resolve_profile_answer(_q("If the previous answer is Yes, please specify who"), profile) == "N/A"
    assert resolve_profile_answer(_q("Se a resposta anterior for sim, informe o nome"), profile) == "N/A"

    # Residência no Brasil
    assert resolve_profile_answer(_q("Are you Brazilian and/or currently living in Brazil?"), profile) == "Yes"
    assert resolve_profile_answer(_q("Você mora no Brasil atualmente?"), profile) == "Sim"

    # Modelo híbrido
    assert resolve_profile_answer(_q("Tem disponibilidade para atuar no modelo híbrido em SP?"), profile) == "Sim"

    # Inglês avançado/fluente binário
    assert resolve_profile_answer(_q("Do you have Advanced to Fluent English skills?"), profile) == "Yes"

    # Ferramentas / plataformas utilizadas atualmente (não deve cair na regra de empresa atual)
    tools_ans = resolve_profile_answer(
        _q("Please share any specific tools and/or platforms you are currently utilizing in your work"),
        profile,
    )
    assert tools_ans is not None
    assert "Python" in tools_ans

    # Experiência técnica binária
    assert resolve_profile_answer(_q("Você possui experiência prática com desenvolvimento em Python?"), profile) == "Sim"
    assert resolve_profile_answer(_q("Possui conhecimento em bancos de dados relacionais?"), profile) == "Sim"
    assert resolve_profile_answer(_q("Tem experiência com metodologias ágeis (Scrum/Kanban)?"), profile) == "Sim"

