"""Testes offline para parsing HTML do LinkedIn/Gupy e resolução de URLs externas."""

from __future__ import annotations

from core.browser_job_reader import (
    parse_gupy_search_html,
    parse_linkedin_search_html,
    resolve_linkedin_external_url,
)


def test_parse_linkedin_search_html_extracts_cards_and_easy_apply():
    html = """
    <ul class="jobs-search__results-list">
      <li data-occludable-job-id="400112233">
        <a class="job-card-list__title" href="/jobs/view/400112233/?trackingId=abc">
          Senior Python Engineer
        </a>
        <span class="job-card-container__primary-description">Nubank</span>
        <li class="job-card-container__metadata-item">São Paulo, Brasil (Remoto)</li>
        <span>Candidatura simplificada</span>
      </li>
      <li data-occludable-job-id="99887766">
        <a class="job-card-list__title" href="/jobs/view/99887766/">
          Backend Developer
        </a>
        <div class="company-name">Acme Corp</div>
        <span class="metadata-item">Híbrido - Rio de Janeiro</span>
      </li>
    </ul>
    """
    jobs = parse_linkedin_search_html(html, limit=10)
    assert len(jobs) == 2

    first = jobs[0]
    assert first["title"] == "Senior Python Engineer"
    assert first["company"] == "Nubank"
    assert first["job_url"] == "https://www.linkedin.com/jobs/view/400112233/"
    assert first["easy_apply"] is True
    assert first["modality"] == "100% Remoto"
    assert first["source_platform"] == "linkedin_browser"

    second = jobs[1]
    assert second["easy_apply"] is False
    assert second["modality"] == "Híbrido"


def test_parse_gupy_search_html_extracts_company_urls_and_modality():
    html = """
    <section>
      <a href="https://stone.gupy.io/jobs/789123?jobBoardSource=gupy_portal">
        <h3>Pessoa Desenvolvedora Python Sênior</h3>
        <p>Stone Pagamentos - 100% Remoto</p>
      </a>
      <a href="https://ifood.gupy.io/jobs/456789">
        <h2>Engenheiro de Software Backend</h2>
        <p>iFood - Híbrido</p>
      </a>
    </section>
    """
    jobs = parse_gupy_search_html(html, limit=10)
    assert len(jobs) == 2

    assert jobs[0]["title"] == "Pessoa Desenvolvedora Python Sênior"
    assert jobs[0]["job_url"] == "https://stone.gupy.io/jobs/789123"
    assert jobs[0]["ats"] == "Gupy"
    assert jobs[0]["external_job_id"] == "gupy-789123"
    assert jobs[0]["modality"] == "100% Remoto"

    assert jobs[1]["job_url"] == "https://ifood.gupy.io/jobs/456789"
    assert jobs[1]["modality"] == "Híbrido"


def test_resolve_linkedin_external_url_decodes_redirect_and_description():
    redir = "https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F42&trk=apply"
    assert resolve_linkedin_external_url(redir) == "https://boards.greenhouse.io/acme/jobs/42"

    direct = "https://jobs.lever.co/acme/1234-5678"
    assert resolve_linkedin_external_url(direct) == direct

    from_desc = resolve_linkedin_external_url(
        "",
        description_text="Candidate-se diretamente em https://jobs.ashbyhq.com/openai/role-123 obrigado.",
    )
    assert from_desc is not None
    assert "ashbyhq.com" in from_desc
