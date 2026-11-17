"""Testes unitários para resolução de currículos (resume_resolver)."""

from pathlib import Path

from core.models import Job, ResumeInfo
from core.resume_resolver import ResumeResolver, normalize, resolve, resolve_bases, split_prefix


def _make_resume(name: str, path_dir: Path) -> tuple[str, ResumeInfo]:
    p = path_dir / name
    return name, ResumeInfo(path=p, summary=f"Summary of {name}", text=f"Text of {name}")


def test_normalization_and_prefix_removal() -> None:
    assert normalize("Currículo Otimizado - Audax Soluções.pdf") == "curriculo otimizado audax solucoes"
    assert normalize("Resume Optimized - CI&T.pdf") == "resume optimized ci t"
    assert normalize("Sinqia / Evertec") == "sinqia evertec"

    core_pt, lang_pt = split_prefix("Currículo Otimizado - Audax Soluções")
    assert core_pt == "audax solucoes"
    assert lang_pt == "pt"

    core_en, lang_en = split_prefix("Resume Optimized - CI&T")
    assert core_en == "ci t"
    assert lang_en == "en"


def test_resolve_exact_and_fuzzy_variations(tmp_path: Path) -> None:
    resumes = dict([
        _make_resume("Currículo Otimizado - Audax Soluções.pdf", tmp_path),
        _make_resume("Resume Optimized - CI&T.pdf", tmp_path),
        _make_resume("Currículo Otimizado - Sinqia Evertec.pdf", tmp_path),
        _make_resume("Resume Optimized - WithProtege AI.pdf", tmp_path),
        _make_resume("Cognition.pdf", tmp_path),
        _make_resume("Base Inglês.pdf", tmp_path),
    ])

    # 1. Audax Soluções (igualdade normalizada direta)
    job_audax = Job(
        id="1", row_index=1, company="Audax Soluções", title="Dev",
        resume_label="Currículo Otimizado - Audax Soluções"
    )
    res = resolve(job_audax, resumes)
    assert res.found is not None
    assert res.filename == "Currículo Otimizado - Audax Soluções.pdf"

    # 2. Sinqia / Evertec vs Sinqia Evertec (barra tratada como espaço)
    job_sinqia = Job(
        id="2", row_index=2, company="Sinqia / Evertec", title="Dev",
        resume_label="Currículo Otimizado - Sinqia / Evertec"
    )
    res = resolve(job_sinqia, resumes)
    assert res.found is not None
    assert res.filename == "Currículo Otimizado - Sinqia Evertec.pdf"

    # 3. WithProtégé AI vs WithProtege AI (acento removido)
    job_protege = Job(
        id="3", row_index=3, company="WithProtégé AI", title="Dev",
        resume_label="Resume Optimized - WithProtégé AI"
    )
    res = resolve(job_protege, resumes)
    assert res.found is not None
    assert res.filename == "Resume Optimized - WithProtege AI.pdf"

    # 4. CI&T
    job_cit = Job(
        id="4", row_index=4, company="CI&T", title="Dev",
        resume_label="Resume Optimized - CI&T"
    )
    res = resolve(job_cit, resumes)
    assert res.found is not None
    assert res.filename == "Resume Optimized - CI&T.pdf"


def test_ambiguity_returns_candidates(tmp_path: Path) -> None:
    resumes = dict([
        _make_resume("Currículo Otimizado - Tech Corp AI.pdf", tmp_path),
        _make_resume("Currículo Otimizado - Tech Corp ML.pdf", tmp_path),
    ])
    job = Job(
        id="5", row_index=5, company="Tech Corp", title="Dev",
        resume_label="Currículo Otimizado - Tech Corp"
    )
    res = resolve(job, resumes, threshold=0.7)
    # Se ambos tiverem scores similares acima do threshold, deve marcar ambiguidade
    if len(res.candidates) > 1:
        assert res.found is None
        assert "ambíguo" in res.reason


def test_missing_or_not_found(tmp_path: Path) -> None:
    resumes = dict([_make_resume("Cognition.pdf", tmp_path)])
    job_none = Job(id="6", row_index=6, company="X", title="Dev", resume_label=None)
    assert resolve(job_none, resumes).found is None

    job_not_found = Job(id="7", row_index=7, company="Inexistente", title="Dev", resume_label="Curriculo Inexistente")
    res = resolve(job_not_found, resumes)
    assert res.found is None


def test_resolve_bases(tmp_path: Path) -> None:
    resumes = dict([
        _make_resume("Currículo Otimizado - Audax.pdf", tmp_path),
        _make_resume("Cognition.pdf", tmp_path),
        _make_resume("Capco.pdf", tmp_path),
        _make_resume("Base Inglês.pdf", tmp_path),
        _make_resume("Base Português.pdf", tmp_path),
    ])
    bases = ["Cognition", "Base Inglês"]
    resolved = resolve_bases(bases, resumes)
    assert "Cognition" in resolved
    assert "Base Inglês" in resolved
    # Arquivo otimizado não deve ser confundido com base
    assert "Currículo Otimizado - Audax.pdf" not in [r.path.name for r in resolved.values()]


def test_load_cache_corrupted_pdf_does_not_crash(tmp_path: Path) -> None:
    corrupted_pdf = tmp_path / "corrupted.pdf"
    corrupted_pdf.write_bytes(b"not a valid pdf content")

    resolver = ResumeResolver(cache_dir=tmp_path)
    resolver.load_cache()
    resumes = resolver.get_resumes()
    assert "corrupted.pdf" not in resumes


def test_load_cache_ignores_docx(tmp_path: Path) -> None:
    docx_file = tmp_path / "resume.docx"
    docx_file.write_bytes(b"fake docx content")

    resolver = ResumeResolver(cache_dir=tmp_path)
    resolver.load_cache()
    resumes = resolver.get_resumes()
    assert "resume.docx" not in resumes


def test_get_base_resumes_excludes_optimized(tmp_path: Path) -> None:
    resumes = dict([
        _make_resume("Currículo Otimizado - Acme.pdf", tmp_path),
        _make_resume("Base Inglês.pdf", tmp_path),
        _make_resume("Base Português.pdf", tmp_path),
    ])
    resolver = ResumeResolver(loaded_resumes=resumes)

    # 1. Job de outra empresa sem base_used especificado
    base_resumes = resolver.get_base_resumes()
    assert "Currículo Otimizado - Acme.pdf" not in base_resumes
    assert "Base Inglês.pdf" in base_resumes
    assert "Base Português.pdf" in base_resumes

    # 2. Job com base_used específico
    base_resumes_filtered = resolver.get_base_resumes(["Base Inglês"])
    assert "Currículo Otimizado - Acme.pdf" not in base_resumes_filtered
    assert "Base Inglês.pdf" in base_resumes_filtered
    assert "Base Português.pdf" not in base_resumes_filtered
