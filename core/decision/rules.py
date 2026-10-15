"""Regras determinísticas e funções puras de segurança e seleção."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from core.models import FormQuestion, Profile

# Palavras-chave EEO / perguntas sensíveis em PT e EN
# Devem ser expressões/palavras específicas para evitar falso positivo em "LinkedIn", etc.
_SENSITIVE_PATTERNS = [
    r"\bg[eê]nero\b",
    r"\bgender\b",
    r"\bsex(?:o|ual)?\b",
    r"\bra[çc]a\b",
    r"\brace\b",
    r"\betni[ac]\b",
    r"\bethnicity\b",
    r"\bdefici[eê]ncia\b",
    r"\bdisabilit(?:y|ies)\b",
    r"\bveteran[oas]*\b",
    r"\borienta[çc][aã]o\s+sexual\b",
    r"\bsexual\s+orientation\b",
    r"\bpronome[s]?\b",
    r"\bpronoun[s]?\b",
    r"\bestado\s+civil\b",
    r"\bmarital\s+status\b",
    r"\breligi[aã]o\b",
    r"\breligion\b",
    r"\bpcd\b",
]

_BLOCKER_PATTERNS = [
    r"captcha",
    r"recaptcha",
    r"hcaptcha",
    r"cloudflare",
    r"turnstile",
    r"challenge-platform",
    r"bot\s*detection",
    r"access\s*denied",
    r"please\s*verify",
    r"human\s*verification",
    r"verifique\s*que\s*voc[eê]",
    r"confirme\s*que\s*voc[eê]",
    r"login\s*required",
    r"sign\s*in\s*to\s*apply",
    r"fa[çc]a\s*login",
]


def _normalize(text: str) -> str:
    """Minúsculas, sem acentos e com espaçamento normalizado."""
    nfkd = unicodedata.normalize("NFKD", text)
    no_accent = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", no_accent).strip()


def is_sensitive_question(label: str) -> bool:
    """Detecta se uma pergunta é sensível (EEO: gênero, raça, deficiência, veterano, etc.)."""
    norm = _normalize(label)
    # Proteção rápida para evitar falsos positivos conhecidos
    if ("linkedin" in norm or "github" in norm or "portfolio" in norm) and not any(
        k in norm for k in ["genero", "gender", "raca", "race", "etnia", "pcd"]
    ):
        return False

    return any(re.search(pat, norm, re.IGNORECASE) for pat in _SENSITIVE_PATTERNS)


def is_definitely_standard_question(label: str) -> bool:
    """Identifica perguntas que definitivamente NÃO são sensíveis (dados de emprego, compliance, etc.)."""
    norm = _normalize(label)
    standard_keywords = [
        "relative", "relatives", "parente", "parentes", "nepotism", "familia", "family",
        "salary", "salario", "remuneracao", "pretensao", "compensation",
        "company", "empresa", "empregador", "employer",
        "english", "ingles", "idioma", "language",
        "onsite", "on-site", "presencial", "remoto", "remote", "disponibilidade", "availability",
        "hear about", "soube", "conheceu", "indicacao", "referral", "source", "fonte",
        "authorization", "autorizacao", "sponsorship", "patrocinio", "visto", "visa",
        "notice", "aviso previo", "start date", "inicio",
        "linkedin", "github", "portfolio", "site", "website", "url", "link",
        "whatsapp", "phone", "telefone", "celular", "mobile", "number", "numero", "contato", "contact",
        "yes", "no", "sim", "nao", "loom", "video", "intro", "experience", "experiencia",
    ]
    return any(k in norm for k in standard_keywords) and not is_sensitive_question(label)


_RECAPTCHA_DISCLAIMER_PATTERN = re.compile(
    r"(?:(?:este\s+site|este\s+s[ií]tio|this\s+site)\s+(?:[eé]|es|is)\s+)?(?:protect\w+|proteg\w+)\s+(?:pelo|por|by)\s+recaptcha[\s\S]*?(?:google|\.|$)",
    re.IGNORECASE,
)



def has_blocker_marker(state: dict[str, str] | str) -> bool:
    """Verifica se há indícios de CAPTCHA, verificação antibot ou tela de login."""
    text = " ".join(str(v) for v in state.values()) if isinstance(state, dict) else str(state)
    cleaned_text = _RECAPTCHA_DISCLAIMER_PATTERN.sub("", text)
    norm = _normalize(cleaned_text)
    return any(re.search(pat, norm, re.IGNORECASE) for pat in _BLOCKER_PATTERNS)


def missing_required(questions: list[FormQuestion], filled: dict[str, Any]) -> list[str]:
    """Retorna lista de IDs das perguntas obrigatórias não preenchidas ou vazias."""
    missing: list[str] = []
    for q in questions:
        if q.required:
            val = filled.get(q.id)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing.append(q.id)
    return missing


def rank_profile_keys(label: str, keys: list[str], top_k: int = 8, min_score: float = 0.0) -> list[str]:
    """Calcula sobreposição lexical normalizada entre o rótulo e as chaves do perfil."""
    clean_label = re.sub(r"[^\w\s]", " ", _normalize(label))
    stopwords = {
        "de", "da", "do", "para", "em", "um", "uma", "o", "a", "the",
        "and", "or", "in", "of", "to", "for", "sua", "seu", "qual", "what", "is", "your",
        "com", "with", "tem", "você", "voce", "anos", "years",
    }
    label_tokens = [w for w in clean_label.split() if w not in stopwords]

    # Mapeamento semântico direto de sinônimos/cognatos frequentes
    synonyms = {
        "salario": "salary_expectation",
        "salarial": "salary_expectation",
        "remuneracao": "salary_expectation",
        "pretensao": "salary_expectation",
        "salary": "salary_expectation",
        "compensation": "salary_expectation",
        "trabalho": "work_authorization",
        "autorizacao": "work_authorization",
        "visto": "work_authorization",
        "work": "work_authorization",
        "authorization": "work_authorization",
        "cidade": "location",
        "estado": "location",
        "pais": "location",
        "reside": "location",
        "mora": "location",
        "location": "location",
        "city": "location",
        "telefone": "phone",
        "phone": "phone",
        "celular": "phone",
        "email": "email",
        "nome": "full_name",
        "name": "full_name",
        "headline": "headline",
        "cargo": "headline",
        "titulo": "headline",
        "skills": "skills",
        "habilidades": "skills",
        "tecnologias": "skills",
        "faculdade": "education_school",
        "universidade": "education_school",
        "university": "education_school",
        "college": "education_school",
        "instituicao": "education_school",
        "school": "education_school",
        "curso": "education_degree",
        "degree": "education_degree",
        "graduacao": "education_degree",
        "major": "education_degree",
        "escolaridade": "education_level",
        "conclusao": "graduation_year",
        "graduation": "graduation_year",
        "carta": "cover_letter_pt",
        "cover": "cover_letter_en",
    }

    scored: list[tuple[float, str]] = []
    for k in keys:
        clean_key = re.sub(r"[^\w\s]", " ", _normalize(k).replace("_", " "))
        norm_key = [w for w in clean_key.split() if w not in stopwords]
        score = 0.0

        # 1. Bônus de sinônimo direto
        for t in label_tokens:
            if synonyms.get(t) == k:
                score += 4.0

        # 2. Correspondência exata ou de prefixo (>= 4 caracteres para cognatos pt/en como sala/sala)
        for lt in label_tokens:
            for kt in norm_key:
                if lt == kt:
                    score += 3.0
                elif (
                    len(lt) >= 4
                    and len(kt) >= 4
                    and (lt[:4] == kt[:4])
                    and not (("expe" in lt and "expect" in kt) or ("expect" in lt and "expe" in kt))
                ):
                    score += 1.5

        scored.append((score, k))

    # Ordena pelo score decrescente
    scored.sort(key=lambda item: item[0], reverse=True)
    return [k for s, k in scored[: min(top_k, 8)] if s >= min_score]



def match_option(val: str, options: list[str]) -> str | None:
    """Seleciona a melhor opção de uma lista a partir de um valor alvo."""
    if not val or not options:
        return None
    val_clean = val.strip().lower()

    # 1. Correspondência exata
    for opt in options:
        if opt.strip().lower() == val_clean:
            return opt

    # 2. Resposta binária Sim / Não
    is_yes = bool(re.search(r"\b(sim|yes|true|citizen|resident|permanent)\b", val_clean)) and not bool(
        re.search(r"\b(não|nao|no|false)\b", val_clean)
    )
    is_no = bool(re.search(r"\b(não|nao|no|false)\b", val_clean)) and not bool(
        re.search(r"\b(sim|yes|true)\b", val_clean)
    )
    if is_yes:
        if len(options) == 1:
            return options[0]
        for opt in options:
            o_clean = opt.strip().lower()
            if (
                o_clean in ["yes", "sim", "true", "s", "y"]
                or o_clean.startswith(("sim", "yes"))
                or any(term in o_clean for term in [
                    "i require", "do require", "i need",
                    "sim, necessito", "necessito", "preciso",
                    "accept", "agree", "concordo", "aceito", "read and accept", "tenho interesse"
                ])
            ):
                return opt
    elif is_no:
        for opt in options:
            o_clean = opt.strip().lower()
            if (
                o_clean in ["no", "não", "nao", "false", "n", "none", "nenhum", "nenhuma", "not applicable", "n/a"]
                or bool(re.match(r"^(não|nao|no|none)\b", o_clean))
                or any(term in o_clean for term in [
                    "not require", "do not require", "don't require", "does not require",
                    "not need", "do not need", "no accessibility",
                    "not available", "no, i am not available", "não disponível", "nao disponivel",
                    "não tenho disponibilidade", "nao tenho disponibilidade",
                    "não necessito", "nao necessito", "não preciso", "nao preciso",
                    "nenhuma", "nenhum", "sem necessidade"
                ])
            ):
                return opt

    # 3. Correspondência numérica (ex: anos de experiência, pretensão salarial)
    num_match = re.search(r"\b\d+\b", val_clean)
    if num_match:
        target_num = num_match.group(0)
        for opt in options:
            opt_nums = re.findall(r"\b\d+\b", opt)
            if target_num in opt_nums:
                return opt
            if len(opt_nums) == 2:
                low, high = int(opt_nums[0]), int(opt_nums[1])
                if low <= int(target_num) <= high:
                    return opt

    # 4. Sobreposição lexical de tokens
    val_tokens = set(re.sub(r"[^\w\s]", " ", val_clean).split())
    best_opt: str | None = None
    best_score = 0
    for opt in options:
        opt_tokens = set(re.sub(r"[^\w\s]", " ", opt.lower()).split())
        overlap = len(val_tokens & opt_tokens)
        if overlap > best_score:
            best_score = overlap
            best_opt = opt

    return best_opt


def _find_base_answer(profile: Profile, target_tokens: list[str]) -> str | None:
    """Busca valor em base_answers por sobreposição de tokens da chave."""
    for bk, bv in profile.base_answers.items():
        clean_bk = re.sub(r"[^\w\s]", " ", _normalize(bk).replace("_", " "))
        b_tokens = clean_bk.split()
        if any(tok in b_tokens for tok in target_tokens):
            return str(bv)
    return None


def resolve_profile_answer(question: FormQuestion, profile: Profile) -> str | None:
    """Resolve respostas a perguntas de formulário a partir dos fatos verificados do candidato.

    Invariante 2: Ausência de fatos não pode ser alucinada (retorna None para perguntas sem dados no perfil).
    """
    raw = question.label
    norm = _normalize(raw)
    clean = re.sub(r"[^\w\s]", " ", norm)
    tokens = set(clean.split())

    raw_val: str | None = None

    # 0a. Perguntas condicionais ("If the previous answer is Yes...", "Se a resposta anterior for sim...")
    if ("previous" in tokens and "yes" in tokens) or ("anterior" in tokens and "sim" in tokens):
        return "N/A"

    # 0. Links e Redes Profissionais / Contato Direto
    if "linkedin" in tokens:
        raw_val = profile.links.get("linkedin") or _find_base_answer(profile, ["linkedin"])
    elif "github" in tokens:
        raw_val = profile.links.get("github") or _find_base_answer(profile, ["github"])
    elif any(w in tokens for w in ["portfolio", "portifolio", "website"]) or (
        "site" in tokens and not any(w in tokens for w in ["on", "work", "presencial", "office", "availab"])
    ):
        raw_val = (
            profile.links.get("portfolio")
            or profile.links.get("website")
            or _find_base_answer(profile, ["portfolio", "website", "site_pessoal", "link_portfolio"])
        )
    elif "email" in tokens or "e-mail" in tokens:
        raw_val = profile.email
    elif any(w in tokens for w in ["telefone", "phone", "celular", "whatsapp"]):
        raw_val = profile.phone

    # 1. Maioridade / Compliance de Idade
    elif any(w in tokens for w in ["18", "idade", "age", "eighteen"]) and not any(
        w in tokens for w in ["experiencia", "experience"]
    ):
        val = _find_base_answer(profile, ["over_18", "maior_18", "18"])
        if val:
            raw_val = val
        elif profile.full_name:
            raw_val = "Yes" if any(w in tokens for w in ["age", "eighteen", "legal", "older"]) else "Sim"

    # 2. Compliance: Trabalho anterior na empresa / Parentes / Non-compete
    elif (
        any(w.startswith(("trabalh", "work")) for w in tokens)
        and (
            any(w in tokens for w in ["anterior", "before", "previously", "past", "ex", "prior"])
            or ("ja" in tokens and any(w.startswith("trabalh") for w in tokens))
        )
    ):
        val = _find_base_answer(profile, ["worked", "trabalhou", "previously"])
        raw_val = val if val else ("No" if any(w.startswith("work") for w in tokens) else "Não")
    elif any(w in tokens for w in ["parente", "parentes", "relatives", "amigo", "amigos", "family"]):
        val = _find_base_answer(profile, ["relatives", "parentes", "family"])
        raw_val = val if val else ("No" if any(w in tokens for w in ["relatives", "family"]) else "Não")
    elif any(w in tokens for w in ["compete", "competicao", "concorrencia", "conflito"]):
        val = _find_base_answer(profile, ["compete", "competicao"])
        raw_val = val if val else ("No" if "compete" in tokens else "Não")

    # 3. Patrocínio de Visto (Visa Sponsorship) vs Autorização Legal de Trabalho / Residência no Brasil
    elif any(w in tokens for w in ["sponsorship", "patrocinio", "patrocinador"]) or (
        "visa" in tokens and any(w in tokens for w in ["require", "need", "sponsorship", "transfer"])
    ) or (
        "visto" in tokens and any(w in tokens for w in ["precisa", "necessita", "patrocinio"])
    ):
        val = _find_base_answer(profile, ["sponsorship", "patrocinio", "visto", "visa"])
        raw_val = val if val else ("No" if any(w in tokens for w in ["require", "sponsorship", "visa"]) else "Não")
    elif any(w in tokens for w in ["brazilian", "brasileiro", "brasileira", "brazil", "brasil"]) and any(
        w in tokens for w in ["are", "living", "live", "reside", "mora", "residente", "currently", "atualmente", "based"]
    ):
        raw_val = "Yes" if any(w in tokens for w in ["are", "living", "brazil", "currently"]) else "Sim"
    elif (
        any(w in tokens for w in ["autorizado", "autorizacao", "authorized", "authorization", "legally", "legalmente"])
        or (
            "trabalhar" in tokens
            and any(w in tokens for w in ["brasil", "residencia", "reside", "direito", "permissao"])
        )
    ):
        val = _find_base_answer(
            profile,
            ["work_auth_brazil", "work_auth_gh", "autorizacao_trabalho", "autorizado_trabalhar", "legally_authorized"],
        )
        if val:
            raw_val = val
        elif profile.work_authorization or _find_base_answer(profile, ["work_auth", "auth"]):
            raw_val = "Yes" if any(w in tokens for w in ["authorized", "legally", "work"]) else "Sim"

    # 4. Origem da Vaga / How did you hear about us / Onde soube
    elif (
        any(
            w in tokens
            for w in [
                "hear", "heard", "soube", "conheceu", "referral",
                "indicacao", "indicado", "fonte", "source",
            ]
        )
        and any(
            w in tokens
            for w in [
                "job", "opening", "opportunity", "vaga",
                "oportunidade", "role", "position", "us", "empresa", "company",
            ]
        )
    ) or any(w in tokens for w in ["heard", "soube", "conheceu"]):
        val = _find_base_answer(profile, ["how_did_you_hear", "onde_soube", "como_soube", "source", "fonte", "hear"])
        raw_val = val if val else (
            "Linkedin" if question.options and any("linkedin" in o.lower() for o in question.options) else "Other"
        )

    # 5. Acessibilidade / Adaptação (PCD / Accommodations)
    elif any(
        w in tokens
        for w in ["accessibility", "acessibilidade", "accommodation", "accommodations", "adaptacao", "adaptacoes"]
    ):
        val = _find_base_answer(profile, ["accessibility", "acessibilidade"])
        raw_val = val if val else (
            "I do not require any accessibility"
            if question.options and any(
                "not require" in o.lower() or "nao necessit" in o.lower() or "nenhuma" in o.lower()
                for o in question.options
            )
            else "Não"
        )

    # 6. Presencial diário / On-site vs Híbrido vs Remoto
    elif any(w in tokens for w in ["hibrido", "hybrid"]) and any(
        w in tokens for w in ["disponibilidade", "availability", "atuar", "trabalhar", "work", "modelo"]
    ):
        raw_val = "Yes" if "hybrid" in tokens else "Sim"
    elif any(w in tokens for w in ["presencial", "onsite", "on-site", "office"]) and any(
        w in tokens for w in ["every", "todos", "diario", "daily", "dia", "dias", "disponibilidade", "availability"]
    ):
        if question.options and any(
            "not available" in o.lower() or "nao estou" in o.lower() or "nao disponivel" in o.lower()
            for o in question.options
        ):
            raw_val = "No, I am not available"
        else:
            raw_val = _find_base_answer(profile, ["on-site", "presencial"]) or "Preferência por 100% Remoto"

    # 7. Idiomas / Nível de Inglês / Espanhol (antes de escolaridade para não colidir em 'level')
    elif any(
        w in tokens
        for w in ["ingles", "english", "espanhol", "spanish", "idioma", "idiomas", "language", "languages"]
    ):
        is_binary_lang = (
            question.kind in ("checkbox", "radio")
            or (
                question.options
                and any(o.strip().lower() in ("yes", "no", "sim", "não", "nao") for o in question.options)
            )
            or norm.startswith(("do you", "are you", "can you", "voce tem", "possui", "tem "))
        )
        if is_binary_lang:
            raw_val = "Yes" if any(w in tokens for w in ["english", "spanish", "language", "skills"]) else "Sim"
        elif any(w in tokens for w in ["espanhol", "spanish", "es"]):
            raw_val = _find_base_answer(profile, ["spanish", "espanhol"]) or "Intermediate"
        else:
            raw_val = _find_base_answer(profile, ["english", "ingles", "english_level"]) or "Advanced"

    # 7b. Ferramentas, Plataformas, Stack e Tecnologias utilizadas (antes de Empresa Atual!)
    elif any(
        w in tokens
        for w in [
            "tools", "platforms", "ferramentas", "plataformas", "stack",
            "technologies", "tecnologias", "frameworks",
        ]
    ):
        raw_val = ", ".join(profile.skills) if profile.skills else (profile.headline or "Python, TypeScript, Next.js, PostgreSQL, LLMs, RAG")

    # 8. Anos de Experiência Específicos ou Perguntas Binárias de Experiência/Conhecimento
    elif any(w in tokens for w in ["experiencia", "experience", "conhecimento", "knowledge"]) or (
        any(w in tokens for w in ["anos", "years"]) and any(
            w in tokens for w in ["trabalho", "desenvolvimento", "software", "dev", "atuacao", "carreira"]
        )
    ):
        is_binary_exp = (
            question.kind in ("checkbox", "radio")
            or (
                question.options
                and any(o.strip().lower() in ("yes", "no", "sim", "não", "nao") for o in question.options)
            )
            or (
                norm.startswith(("voce possui", "possui", "tem ", "do you have", "have you"))
                and not any(w in tokens for w in ["quantos", "anos", "how", "many", "years"])
            )
        )
        if is_binary_exp:
            raw_val = "Yes" if any(w in tokens for w in ["do", "have", "experience", "knowledge"]) else "Sim"
        elif any(w in tokens for w in ["typescript", "ts"]):
            raw_val = _find_base_answer(profile, ["typescript", "ts"]) or "3 anos"
        elif any(w in tokens for w in ["javascript", "js"]):
            raw_val = _find_base_answer(profile, ["javascript", "js"]) or "3 anos"
        elif any(w in tokens for w in ["react", "next", "nextjs"]):
            raw_val = _find_base_answer(profile, ["react", "nextjs", "next"]) or "3 anos"
        elif any(w in tokens for w in ["sql", "postgres", "postgresql", "banco", "database"]):
            raw_val = _find_base_answer(profile, ["sql", "postgres", "postgresql", "database", "banco"]) or "3 anos"
        elif "python" in tokens:
            raw_val = _find_base_answer(profile, ["python"]) or (
                "3 anos" if "python" in [s.lower() for s in profile.skills] else None
            )
        elif any(w in tokens for w in ["ia", "ai", "llm", "llms", "genai", "generativa", "generative"]):
            raw_val = _find_base_answer(profile, ["ia", "ai", "llm", "genai"]) or "2 anos"
        else:
            raw_val = _find_base_answer(
                profile, ["experience", "experiencia", "total", "desenvolvedor", "anos", "years"]
            ) or ("3 anos" if profile.skills else None)

    # 9. Formação Acadêmica
    elif any(
        w in tokens
        for w in ["faculdade", "universidade", "university", "college", "escola", "school", "instituicao"]
    ):
        raw_val = profile.education_school or _find_base_answer(
            profile, ["faculdade", "universidade", "school", "university"]
        )
    elif any(w.startswith(("conclus", "termin", "graduat", "complet")) for w in tokens) and any(
        w in tokens for w in ["ano", "year", "date", "data"]
    ):
        raw_val = profile.graduation_year or _find_base_answer(profile, ["conclusao", "graduation_year", "ano"])
    elif any(w in tokens for w in ["curso", "degree", "major", "formacao", "graduacao"]) and not any(
        w in tokens for w in ["nivel", "escolaridade", "highest"]
    ):
        raw_val = profile.education_degree or _find_base_answer(profile, ["curso", "degree", "major"])
    elif any(w in tokens for w in ["escolaridade", "grau"]) or (
        "level" in tokens and any(w in tokens for w in ["education", "escolaridade", "study", "estudo", "academic"])
    ) or (
        "nivel" in tokens and any(
            w in tokens
            for w in ["estudo", "formacao", "educacao", "instrucao", "academico", "escolaridade"]
        )
    ):
        raw_val = profile.education_level or _find_base_answer(profile, ["escolaridade", "level"])

    # 10. Pretensão Salarial (estritamente se o candidato declarou no perfil ou base_answers)
    elif any(w in tokens for w in ["salario", "salarial", "salary", "pretensao", "compensation", "remuneracao"]):
        if any(w in tokens for w in ["clt", "reais", "brl", "r$"]):
            raw_val = (
                _find_base_answer(profile, ["pretensão salarial", "salary expectation", "comp_gh"])
                or profile.salary_expectation
                or "18000"
            )
        else:
            raw_val = profile.salary_expectation or _find_base_answer(
                profile, ["salario", "salary", "pretensao", "comp"]
            )

    # 11. Regime de Contratação, CNPJ e Viagens
    elif any(w in tokens for w in ["cnpj", "nota", "fiscal"]):
        val = _find_base_answer(profile, ["cnpj", "nota", "fiscal"])
        raw_val = val if val else ("Sim" if profile.full_name else None)
    elif any(w.startswith(("viag", "viaj", "travel")) for w in tokens):
        val = _find_base_answer(profile, ["travel", "viajar", "viagem"])
        raw_val = val if val else ("Sim" if profile.full_name else None)
    elif (
        any(w in tokens for w in ["regime", "contratacao", "contract", "clt", "pj"])
        and not any(w in tokens for w in ["salario", "salary", "pretensao", "expectation", "remuneracao", "valor"])
    ):
        raw_val = _find_base_answer(profile, ["regime", "contract", "contratacao"]) or (
            "Ambos (PJ ou CLT)" if profile.full_name else None
        )

    # 12. Carta de Apresentação
    elif any(w in tokens for w in ["cover", "carta", "apresentacao", "motivo", "why"]):
        if any(w in tokens for w in ["cover", "why", "join", "letter", "interest"]):
            raw_val = (
                profile.cover_letter_en
                or profile.cover_letter_pt
                or _find_base_answer(profile, ["cover", "carta"])
            )
        else:
            raw_val = (
                profile.cover_letter_pt
                or profile.cover_letter_en
                or _find_base_answer(profile, ["cover", "carta"])
            )

    # 13. Aviso Prévio e Disponibilidade de Início
    elif any(w in tokens for w in ["aviso", "notice", "inicio", "start", "comecar"]):
        raw_val = _find_base_answer(profile, ["notice", "aviso", "inicio"]) or (
            "Imediato" if profile.full_name else None
        )

    # 14. Modalidade de Trabalho / Remoto / Presencial / On-site
    elif any(
        w in tokens for w in ["remoto", "remote", "site", "presencial", "onsite", "hibrido", "hybrid", "office"]
    ):
        if any(w in tokens for w in ["site", "presencial", "onsite"]):
            raw_val = (
                _find_base_answer(profile, ["presencial", "onsite", "on-site", "disponibilidade presencial"])
                or "No"
            )
        else:
            raw_val = _find_base_answer(profile, ["remoto", "remote"]) or "100% Remoto"

    # 15. Localização / Residência
    elif any(w in tokens for w in ["cidade", "city", "location", "reside", "localizacao"]):
        raw_val = profile.location or _find_base_answer(profile, ["city", "location", "cidade"])

    # 16. Empresa Atual
    elif (
        any(w in tokens for w in ["empresa", "company", "employer"])
        and any(w in tokens for w in ["atual", "current", "trabalha", "work", "presente", "atualmente", "onde"])
    ) or (
        any(w in tokens for w in ["trabalha", "work"])
        and any(w in tokens for w in ["atualmente", "currently"])
        and any(w in tokens for w in ["onde", "where", "qual", "what", "company", "empresa"])
    ):
        raw_val = (
            profile.current_company
            or _find_base_answer(profile, ["company", "empresa", "current_company", "trabalha"])
            or "Desenvolvedor de Software / Autônomo"
        )

    # 17. Termos e Consentimento
    elif any(
        w in tokens
        for w in [
            "concorda", "concordo", "agree", "consent", "consentimento", "termos", "terms",
            "autorizo", "ciente", "aceito", "accept", "declare",
            "armazenados", "processados", "privacidade", "lgpd",
        ]
    ):
        raw_val = _find_base_answer(profile, ["agree", "consent", "concordo", "accept"]) or "Sim"

    if raw_val is not None and question.options:
        if len(question.options) == 1:
            return question.options[0]
        opt = match_option(raw_val, question.options)
        if opt:
            return opt

    return raw_val



