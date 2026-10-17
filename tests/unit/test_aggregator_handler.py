"""Testes unitários para o AggregatorHandler (Indeed, Remote Rocketship, etc.)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation.handlers.aggregator import AggregatorHandler
from core.models import AnswerSet, BlockerDetected, Job, Profile, ResumeInfo, TaskStatus


def _make_job(url: str, company: str = "Empresa Teste") -> Job:
    return Job(
        id="job-agg",
        row_index=1,
        company=company,
        title="AI Engineer",
        url=url,
    )


def test_can_handle() -> None:
    handler = AggregatorHandler()

    # Domínios de agregadores suportados
    assert handler.can_handle(_make_job("https://br.indeed.com/vagas/1")) is True
    assert handler.can_handle(_make_job("https://www.remoterocketship.com/vaga/2")) is True
    assert handler.can_handle(_make_job("https://dailyremote.com/job/3")) is True
    assert handler.can_handle(_make_job("https://remotar.com.br/job/4")) is True
    assert handler.can_handle(_make_job("https://www.linkedin.com/jobs/view/123456")) is True
    assert handler.can_handle(_make_job("https://programathor.com.br/jobs/789")) is True
    assert handler.can_handle(_make_job("https://weworkremotely.com/remote-jobs/101")) is True

    # ATSs específicas devem ir para seus próprios handlers
    assert handler.can_handle(_make_job("https://jobs.lever.co/empresa/5")) is False
    assert handler.can_handle(_make_job("https://boards.greenhouse.io/empresa/6")) is False

    # Outras ATSs desconhecidas
    assert handler.can_handle(_make_job("https://myworkdayjobs.com/apply")) is False


@pytest.mark.asyncio
async def test_inspect_detects_cloudflare_blocker() -> None:
    handler = AggregatorHandler()
    job = _make_job("https://br.indeed.com/vaga/1")

    page = MagicMock()
    page.url = "https://br.indeed.com/vaga/1"
    page.title = AsyncMock(return_value="Blocked - Indeed.com")
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    locator_mock = MagicMock()
    locator_mock.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=locator_mock)

    with pytest.raises(BlockerDetected) as exc_info:
        await handler.inspect(page, job)

    assert "Bloqueio anti-bot" in str(exc_info.value) or "Cloudflare" in str(exc_info.value)


@pytest.mark.asyncio
async def test_inspect_delegates_to_lever_outbound() -> None:
    handler = AggregatorHandler()
    job = _make_job("https://www.remoterocketship.com/vaga/10")

    page = MagicMock()
    page.url = "https://www.remoterocketship.com/vaga/10"
    page.title = AsyncMock(return_value="Vaga Remota - Remote Rocketship")
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    def locator_side_effect(selector: str):
        loc = MagicMock()
        if "lever.co" in selector:
            loc.count = AsyncMock(return_value=1)
            first_mock = MagicMock()
            first_mock.get_attribute = AsyncMock(return_value="https://jobs.lever.co/acme/123")
            loc.first = first_mock
        else:
            loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=locator_side_effect)

    # Mock do LeverHandler inspect
    handler._lever.inspect = AsyncMock()
    handler._lever.inspect.return_value = MagicMock(description="Lever Job")

    info = await handler.inspect(page, job)
    assert job.id in handler._delegated
    assert handler._delegated[job.id][0] == handler._lever
    assert info.description == "Lever Job"


@pytest.mark.asyncio
async def test_fill_and_submit_dry_run() -> None:
    handler = AggregatorHandler()
    job = _make_job("https://remotar.com.br/vaga/1")
    profile = Profile(
        full_name="Ana Silva",
        email="ana@exemplo.com",
        phone="11999999999",
        location="São Paulo, SP",
    )
    resume = ResumeInfo(path=Path("dummy.pdf"), summary="Resumo", text="Texto")
    answers = AnswerSet(answers={})

    page = MagicMock()
    page.url = "https://remotar.com.br/vaga/1"

    resume_loc = MagicMock()
    resume_loc.count = AsyncMock(return_value=1)
    resume_loc.first = MagicMock()
    resume_loc.first.set_input_files = AsyncMock()

    def locator_side_effect(selector: str):
        if "input[required]" in selector:
            empty_req = MagicMock()
            empty_req.count = AsyncMock(return_value=0)
            return empty_req
        if selector == handler.selectors.RESUME:
            return resume_loc
        default_loc = MagicMock()
        default_loc.count = AsyncMock(return_value=0)
        return default_loc

    page.locator = MagicMock(side_effect=locator_side_effect)

    result = await handler.fill_and_submit(
        page=page,
        job=job,
        profile=profile,
        resume=resume,
        answers=answers,
        dry_run=True,
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.dry_run is True
    assert result.submitted is False


@pytest.mark.asyncio
async def test_aggregator_release_cleans_delegated() -> None:
    """REF-3: release() deve limpar self._delegated para o job especificado."""
    handler = AggregatorHandler()
    job = _make_job("https://remotar.com.br/vaga/1")
    handler._delegated[job.id] = (handler._lever, job)

    assert job.id in handler._delegated
    handler.release(job)
    assert job.id not in handler._delegated


@pytest.mark.asyncio
async def test_aggregator_missing_resume_returns_needs_review() -> None:
    """SAFE-3: Campo de upload não encontrado devolve NEEDS_REVIEW com submitted=False."""
    handler = AggregatorHandler()
    job = _make_job("https://remotar.com.br/vaga/1")
    profile = Profile(
        full_name="Ana Silva",
        email="ana@exemplo.com",
        phone="11999999999",
        location="São Paulo, SP",
    )
    resume = ResumeInfo(path=Path("dummy.pdf"), summary="Resumo", text="Texto")

    page = MagicMock()
    page.url = "https://remotar.com.br/vaga/1"
    loc_empty = MagicMock()
    loc_empty.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=loc_empty)

    result = await handler.fill_and_submit(
        page=page,
        job=job,
        profile=profile,
        resume=resume,
        answers=AnswerSet(),
        dry_run=True,
    )

    assert result.status == TaskStatus.NEEDS_REVIEW
    assert result.submitted is False


@pytest.mark.asyncio
async def test_aggregator_skips_photo_and_video_file_inputs() -> None:
    handler = AggregatorHandler()
    assert handler.can_handle(_make_job("https://www.kake.co/jobs/123")) is True
    assert handler.can_handle(_make_job("https://dreamhub.knnidiomas.com.br/vaga/123")) is True

    page = MagicMock()
    photo_input = MagicMock()
    photo_input.get_attribute = AsyncMock(
        side_effect=lambda attr: "photo" if attr == "id" else ("image/png,image/jpeg" if attr == "accept" else "")
    )
    photo_input.evaluate = AsyncMock(return_value="")
    assert await handler._should_skip_file_input(page, photo_input) is True

    resume_input = MagicMock()
    resume_input.get_attribute = AsyncMock(
        side_effect=lambda attr: "resume" if attr == "id" else (".pdf,.doc,.docx" if attr == "accept" else "")
    )
    resume_input.evaluate = AsyncMock(return_value="Upload Resume / CV")
    assert await handler._should_skip_file_input(page, resume_input) is False


def test_can_handle_new_aggregator_domains() -> None:
    handler = AggregatorHandler()
    for url in (
        "https://bebee.com/br/jobs/dev-1",
        "https://eu.dev.br/vagas/area/data-ai/",
        "https://www.turing.com/pt/jobs/vagas-engenheiro-ia",
        "https://br.trabajo.org/emprego-4112",
        "https://www.prototipeai.com/job_postings/eng-ia",
        "https://jobgether.com/offer/123",
        "https://empregos.recrutei.com.br/",
        "https://amazon.jobs/pt/jobs/10471103/sr-software-engineer",
        "https://www.recrutasimples.com.br/vaga/dev-ai-first",
        "https://www.devvagas.com.br/",
        "https://www.estadao.com.br/tecmundo/trabalho-remoto/",
    ):
        assert handler.can_handle(_make_job(url)) is True


@pytest.mark.asyncio
async def test_remotar_next_data_outbound_extraction() -> None:
    handler = AggregatorHandler()
    page = MagicMock()
    page.url = "https://remotar.com.br/job/165556/extractta/engenheiro-de-ia-generativa"

    next_script_loc = MagicMock()
    next_script_loc.count = AsyncMock(return_value=1)
    next_script_loc.first = MagicMock()
    next_script_loc.first.inner_text = AsyncMock(
        return_value='{"props":{"pageProps":{"jobData":{"externalLink":"https://extractta.inhire.app/vagas/e4f6427d/eng"}}}}'
    )

    empty_loc = MagicMock()
    empty_loc.count = AsyncMock(return_value=0)

    def locator_side_effect(selector: str):
        if selector == "script#__NEXT_DATA__":
            return next_script_loc
        return empty_loc

    page.locator = MagicMock(side_effect=locator_side_effect)
    outbound = await handler._find_outbound_apply_url(page)
    assert outbound == "https://extractta.inhire.app/vagas/e4f6427d/eng"


@pytest.mark.parametrize(
    ("company", "url", "expected_handled"),
    [
        ("Sierra Studio", "https://jobs.ashbyhq.com/sierra-studio/64d8bf9f-9c84-469c-bff3-e15c541e9bd0/application", True),
        ("Sinqia / Evertec", "https://jobs.quickin.io/sinqia/apply?job_id=6ac3e23e8759e00013ac4841", True),
        ("Kognit", "https://kognit.vagas.solides.com.br/vaga/929436", True),
        ("Jump", "https://remotar.com.br/job/165249/jump/engenheiro-de-ia", True),
        ("Desconhecida", "https://unsupported-ats-domain.org/apply/123", False),
    ],
)
def test_aggregator_can_handle_specialized_ats(company: str, url: str, expected_handled: bool) -> None:
    """Valida cenários parametrizados de agregadores e portais (Ashby, Quickin, Sólides, Kognit, Jump)."""
    handler = AggregatorHandler()
    job = _make_job(url=url, company=company)
    assert handler.can_handle(job) is expected_handled


def test_aggregator_ashby_systemfield_resume_selector() -> None:
    """Valida se o seletor de currículo do Aggregator captura os campos de sistema do Ashby."""
    handler = AggregatorHandler()
    assert "_systemfield_resume" in handler.selectors.RESUME
    assert "curriculo" in handler.selectors.RESUME


def test_aggregator_solides_apply_button_selector() -> None:
    """Valida se o seletor de submit/apply do Aggregator cobre botões típicos da Sólides e Quickin."""
    handler = AggregatorHandler()
    assert "Candidatar" in handler.selectors.SUBMIT_BTN
    assert "Continuar" in handler.selectors.SUBMIT_BTN or "Continue" in handler.selectors.SUBMIT_BTN
