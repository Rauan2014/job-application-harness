"""Testes unitários e de API FastAPI para o modo visual ao vivo da Laya."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import web.app as web_app
import web.config_manager as cm
from automation.base_handler import BaseATSHandler
from automation.laya_navigator import (
    LayaGenericHandler,
    LayaWebNavigator,
    SemanticSnapshot,
    VisualLocatorProxy,
    VisualPageProxy,
    WebElementNode,
)
from automation.registry import register_handler, reset_registry
from config.settings import Settings
from core.decision.layer import DecisionLayer
from core.decision.models import ChoiceVerdict, NoulVerdict
from core.models import AnswerSet, BlockerDetected, Job, Profile, ResumeInfo, TaskStatus
from core.pipeline import Pipeline
from web.runner import HarnessRunner, global_runner


class _FakeLocator:
    def __init__(self, selector: str) -> None:
        self.selector = selector
        self.calls: list[tuple[str, Any]] = []

    @property
    def first(self) -> _FakeLocator:
        return self

    @property
    def last(self) -> _FakeLocator:
        return self

    def nth(self, idx: int) -> _FakeLocator:
        _ = idx
        return self

    async def click(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("click", args, kwargs))

    async def fill(self, value: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("fill", value, args, kwargs))

    async def select_option(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("select_option", args, kwargs))

    async def check(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("check", args, kwargs))

    async def uncheck(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("uncheck", args, kwargs))

    async def set_input_files(self, files: Any, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("set_input_files", files, args, kwargs))


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://sandbox.example/job/1"
        self.eval_calls: list[Any] = []
        self.waits: list[int] = []
        self.locators: dict[str, _FakeLocator] = {}

    def locator(self, selector: str, *args: Any, **kwargs: Any) -> _FakeLocator:
        _ = args, kwargs
        if selector not in self.locators:
            self.locators[selector] = _FakeLocator(selector)
        return self.locators[selector]

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.eval_calls.append((script[:40], arg))
        return None

    async def screenshot(self, *, type: str = "jpeg", quality: int = 62) -> bytes:
        _ = type, quality
        return b"\xff\xd8\xffFAKEJPEG"

    async def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _StubModel:
    async def noul(self, state: dict[str, str], instructions: str, criteria: Any = None) -> NoulVerdict:
        _ = state, instructions, criteria
        return NoulVerdict(p_true=0.92, band="yes")

    async def choice(
        self,
        state: dict[str, str],
        instructions: str,
        options: dict[str, str],
    ) -> ChoiceVerdict:
        _ = state
        if "opção" in instructions.lower():
            first_key = next(iter(options))
            return ChoiceVerdict(probs={k: 0.5 for k in options}, top=first_key, margin=0.01, order_consistent=False)
        first_key = next(iter(options))
        return ChoiceVerdict(probs={first_key: 0.95}, top=first_key, margin=0.9, order_consistent=True)


class _StubMatcher:
    async def _ask_json(self, messages: Any, parse: Any) -> Any:
        _ = messages
        return parse({"choice": "opt_1"})


@pytest.mark.asyncio
async def test_wrap_page_bypasses_when_visual_watch_disabled() -> None:
    nav = LayaWebNavigator(visual_watch=False)
    fake = _FakePage()
    assert nav.wrap_page(fake) is fake  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_visual_proxy_highlights_and_emits_events_with_jpeg() -> None:
    events: list[dict[str, Any]] = []
    nav = LayaWebNavigator(
        decision_model=_StubModel(),
        matcher=_StubMatcher(),  # type: ignore[arg-type]
        visual_watch=True,
        visual_delay_ms=120,
        on_visual_event=events.append,
    )
    raw = _FakePage()
    wrapped = nav.wrap_page(raw)  # type: ignore[arg-type]
    assert isinstance(wrapped, VisualPageProxy)

    assert isinstance(wrapped.locator("[data-laya-id='el_1']"), VisualLocatorProxy)
    assert isinstance(wrapped.locator(".g-recaptcha"), _FakeLocator)

    loc = wrapped.locator("[data-laya-id='el_1']")
    await loc.fill("Ana Clara")
    await loc.select_option(label="Pleno")
    await loc.check()
    await loc.set_input_files("curriculo.pdf")
    await loc.click()

    assert [e["title"] for e in events] == [
        "AÇÃO · PREENCHER",
        "AÇÃO · SELECT",
        "AÇÃO · CHECK / RADIO",
        "AÇÃO · UPLOAD PDF",
        "AÇÃO · CLIQUE",
    ]
    assert all(e["kind"] == "action" for e in events)
    assert all(e["frame_bytes"] == b"\xff\xd8\xffFAKEJPEG" for e in events)
    assert all(e["url"] == "https://sandbox.example/job/1" for e in events)
    assert raw.waits == [120, 120, 120, 120, 120]


@pytest.mark.asyncio
async def test_navigator_emits_s1_s2_and_eeo_visual_events() -> None:
    events: list[dict[str, Any]] = []
    handler = LayaGenericHandler(
        decision_model=_StubModel(),
        matcher=_StubMatcher(),  # type: ignore[arg-type]
        visual_watch=True,
        visual_delay_ms=0,
        on_visual_event=events.append,
    )
    raw = _FakePage()
    wrapped = handler.wrap_visual_page(raw)  # type: ignore[arg-type]

    btn = WebElementNode(
        eid="el_cta",
        tag="button",
        role="button",
        label="Candidatar-se",
        selector="[data-laya-id='el_cta']",
    )
    sel_node = WebElementNode(
        eid="el_sel",
        tag="select",
        role="combobox",
        label="Modalidade",
        selector="[data-laya-id='el_sel']",
        options=["Presencial", "Remoto"],
    )
    eeo_node = WebElementNode(
        eid="el_eeo",
        tag="select",
        role="combobox",
        label="Raça / Etnia",
        selector="[data-laya-id='el_eeo']",
        options=["Parda", "Branca"],
    )
    snap = SemanticSnapshot(
        url=raw.url,
        title="Vaga",
        elements=[btn, sel_node, eeo_node],
    )

    has_form, by = await handler.navigator.decide_page_has_form(snap)
    assert has_form is True and by == "laya"

    chosen, nav_by = await handler.navigator.choose_navigation_action(snap, goal="open_form")
    assert chosen is not None and nav_by == "laya"

    profile = Profile(
        full_name="Ana",
        email="ana@example.com",
        phone="11999990000",
        location="SP",
        eeo={"race": "Parda"},
    )
    opt = await handler.navigator.resolve_select_option(
        label="Modalidade preferida",
        options=["Presencial", "Remoto"],
        desired_value="Trabalho 100% online",
        profile=profile,
    )
    assert opt == "Remoto"

    selected_eeo: list[str] = []

    async def _eeo_chooser(loc: Any, label: str, eeo: dict[str, str]) -> None:
        _ = label, eeo
        await loc.select_option(label="Parda")
        selected_eeo.append("Parda")

    await handler.navigator._fill_sensitive_field(
        page=wrapped,
        node=eeo_node,
        label="Raça / Etnia",
        profile=profile,
        answers=AnswerSet(),
        eeo_chooser=_eeo_chooser,
    )

    assert selected_eeo == ["Parda"]
    kinds = [e["kind"] for e in events]
    assert "laya" in kinds
    assert "llm" in kinds
    assert "eeo" in kinds
    assert "action" in kinds
    assert handler.navigator.eeo_hits == 1


def test_config_manager_persists_laya_visual_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LLM_API_KEY=sk-test-123",
                "LLM_BASE_URL=https://api.example.com/v1",
                "LLM_MODEL=gpt-test",
                "DRIVE_FOLDER_ID=drv-123",
                "LAYA_WEB_NAVIGATOR_ENABLED=false",
                "LAYA_VISUAL_WATCH=false",
                "LAYA_VISUAL_DELAY_MS=750",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cm, "ENV_FILE_PATH", env_file)
    monkeypatch.setenv("LLM_API_KEY", "sk-test-123")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    monkeypatch.setenv("DRIVE_FOLDER_ID", "drv-123")
    monkeypatch.setenv("LAYA_WEB_NAVIGATOR_ENABLED", "false")
    monkeypatch.setenv("LAYA_VISUAL_WATCH", "false")
    monkeypatch.setenv("LAYA_VISUAL_DELAY_MS", "750")

    cfg0 = cm.get_ui_config()
    assert cfg0["decision"]["laya_web_navigator_enabled"] is False
    assert cfg0["decision"]["laya_visual_watch"] is False
    assert cfg0["decision"]["laya_visual_delay_ms"] == 750

    res = cm.save_ui_config(
        {
            "decision": {
                "laya_web_navigator_enabled": True,
                "laya_visual_watch": True,
                "laya_visual_delay_ms": 420,
            }
        }
    )
    assert res["decision"]["laya_web_navigator_enabled"] is True
    assert res["decision"]["laya_visual_watch"] is True
    assert res["decision"]["laya_visual_delay_ms"] == 420

    cfg1 = cm.get_ui_config()
    assert cfg1["decision"]["laya_web_navigator_enabled"] is True
    assert cfg1["decision"]["laya_visual_watch"] is True
    assert cfg1["decision"]["laya_visual_delay_ms"] == 420


def test_runner_and_fastapi_laya_live_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = HarnessRunner()
    runner.emit_laya_event(
        {
            "title": "SYSTEM 1 · LAYA",
            "detail": "Botão Candidatar-se identificado",
            "kind": "laya",
            "s1": 3,
            "s2": 1,
            "eeo": 1,
            "url": "https://portal.example/vaga",
            "engine": "Laya Sandbox",
            "frame_bytes": b"\xff\xd8\xffJPEG",
        }
    )
    status = runner.get_status()["laya_live"]
    assert status["frame_seq"] == 1
    assert status["has_frame"] is True
    assert status["stats"] == {"s1": 3, "s2": 1, "eeo": 1}
    assert "frame_bytes" not in status["events"][0]

    prev_frame = global_runner.laya_latest_frame_bytes
    prev_running = global_runner.is_running
    try:
        global_runner.laya_latest_frame_bytes = None
        client = TestClient(web_app.app)

        assert client.get("/api/laya/live-frame").status_code == 204

        global_runner.laya_latest_frame_bytes = b"\xff\xd8\xff\xe0TEST"
        resp_img = client.get("/api/laya/live-frame?seq=1")
        assert resp_img.status_code == 200
        assert resp_img.headers["content-type"].startswith("image/jpeg")
        assert resp_img.content == b"\xff\xd8\xff\xe0TEST"

        index_resp = client.get("/")
        assert index_resp.status_code == 200
        html = index_resp.text
        assert 'id="laya-live-card"' in html
        assert 'id="btn-laya-demo"' in html
        assert 'id="laya-real-job-select"' in html
        assert 'id="laya-real-url-input"' in html
        assert 'id="laya-real-dryrun"' in html
        assert 'id="btn-laya-real-run"' in html
        assert 'id="run-opt-laya-visual"' in html
        assert 'id="cfg-laya-visual"' in html

        async def _ok_demo(options: dict[str, Any] | None = None) -> dict[str, Any]:
            _ = options
            return {"success": True, "message": "ok"}

        async def _busy_demo(options: dict[str, Any] | None = None) -> dict[str, Any]:
            _ = options
            return {"success": False, "error": "busy"}

        monkeypatch.setattr(global_runner, "start_laya_demo", _ok_demo)
        assert client.post("/api/laya/demo-run", json={"headless": True}).status_code == 200

        monkeypatch.setattr(global_runner, "start_laya_demo", _busy_demo)
        assert client.post("/api/laya/demo-run", json={"headless": True}).status_code == 409

        # Validação do endpoint de vaga real ao vivo (/api/laya/run-real)
        assert client.post("/api/laya/run-real", json={}).status_code == 400
        assert client.post("/api/laya/run-real", json={"custom_url": "file:///etc/passwd"}).status_code == 400

        captured_opts: list[dict[str, Any]] = []

        async def _ok_real_start(opts: dict[str, Any]) -> dict[str, Any]:
            captured_opts.append(opts)
            return {"success": True, "message": "started"}

        monkeypatch.setattr(global_runner, "start", _ok_real_start)
        resp_real = client.post(
            "/api/laya/run-real",
            json={
                "custom_url": "https://boards.greenhouse.io/acme/jobs/123",
                "dry_run": True,
                "headless": False,
                "force_laya_generic": True,
            },
        )
        assert resp_real.status_code == 200
        assert captured_opts[0]["custom_url"] == "https://boards.greenhouse.io/acme/jobs/123"
        assert captured_opts[0]["dry_run"] is True
        assert captured_opts[0]["headless"] is False
        assert captured_opts[0]["laya_visual"] is True
        assert captured_opts[0]["force_laya_generic"] is True
        assert captured_opts[0]["single_job_watch"] is True
        assert captured_opts[0]["concurrency"] == 1
    finally:
        global_runner.laya_latest_frame_bytes = prev_frame
        global_runner.is_running = prev_running


class _ProbeLocator(_FakeLocator):
    def __init__(self, selector: str, *, visible: bool = True, count_val: int = 1) -> None:
        super().__init__(selector)
        self._visible = visible
        self._count_val = count_val

    async def count(self) -> int:
        return self._count_val

    async def is_visible(self) -> bool:
        return self._visible


class _ProbePage(_FakePage):
    def __init__(self) -> None:
        super().__init__()
        self.visited: list[str] = []

    async def goto(self, url: str, *args: Any, **kwargs: Any) -> str:
        _ = args, kwargs
        self.url = url
        self.visited.append(url)
        return "ok"

    def get_by_role(self, role: str, **kwargs: Any) -> _ProbeLocator:
        return _ProbeLocator(f"role={role}:{kwargs}", visible=True, count_val=1)

    def get_by_label(self, label: str, **kwargs: Any) -> _ProbeLocator:
        _ = kwargs
        return _ProbeLocator(f"label={label}", visible=True, count_val=1)

    def get_by_text(self, text: str, **kwargs: Any) -> _ProbeLocator:
        _ = kwargs
        return _ProbeLocator(f"text={text}", visible=True, count_val=1)

    def get_by_placeholder(self, text: str, **kwargs: Any) -> _ProbeLocator:
        _ = kwargs
        return _ProbeLocator(f"placeholder={text}", visible=True, count_val=1)

    def get_by_test_id(self, test_id: str, **kwargs: Any) -> _ProbeLocator:
        _ = kwargs
        return _ProbeLocator(f"testid={test_id}", visible=True, count_val=1)


@pytest.mark.asyncio
async def test_universal_visual_proxy_captures_goto_any_selector_and_skips_hidden() -> None:
    events: list[dict[str, Any]] = []
    nav = LayaWebNavigator(
        visual_watch=True,
        visual_delay_ms=0,
        engine_label="ATS Handler · lever",
        on_visual_event=events.append,
    )
    raw = _ProbePage()
    raw.locators["input[name='email']"] = _ProbeLocator("input[name='email']", visible=True, count_val=1)
    raw.locators[".hidden-probe"] = _ProbeLocator(".hidden-probe", visible=False, count_val=0)

    wrapped = nav.wrap_page(raw, intercept_all=True)  # type: ignore[arg-type]
    await wrapped.goto("https://jobs.lever.co/acme/123")
    await wrapped.locator("input[name='email']").fill("ana@example.com")
    await wrapped.get_by_role("button", name="Apply").click()
    await wrapped.locator(".hidden-probe").click()

    titles = [e["title"] for e in events]
    assert titles == [
        "NAVEGAÇÃO · PÁGINA ABERTA",
        "AÇÃO · PREENCHER",
        "AÇÃO · CLIQUE",
    ]
    assert events[0]["url"] == "https://jobs.lever.co/acme/123"
    assert len(raw.locators[".hidden-probe"].calls) == 1


@pytest.mark.asyncio
async def test_pipeline_blocker_emits_terminal_visual_frame_without_freezing(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    probe_page = _ProbePage()
    pdf_path = tmp_path / "cv.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    resume = ResumeInfo(path=pdf_path, summary="Python", text="Python")

    class _DummyResolver:
        async def resolve(self, _job: Job) -> ResumeInfo:
            return resume

        def get_base_resumes(self, _used: list[str]) -> list[ResumeInfo]:
            return [resume]

        def get_resumes(self) -> dict[str, ResumeInfo]:
            return {"cv": resume}

    class _DummyBrowserMgr:
        @asynccontextmanager
        async def new_page(self):  # type: ignore[no-untyped-def]
            yield probe_page

    class _DummyMatcher:
        def get_and_reset_job_tokens(self) -> dict[str, int]:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    class _ExpiredInhireHandler(BaseATSHandler):
        name = "aggregator"

        def can_handle(self, job: Job) -> bool:
            _ = job
            return True

        async def inspect(self, page: Any, job: Job) -> Any:
            await page.goto(str(job.url))
            raise BlockerDetected(f"Vaga encerrada ou expirada no portal: '{job.url}'")

        async def fill_and_submit(
            self,
            page: Any,
            job: Job,
            profile: Profile,
            resume: ResumeInfo,
            answers: AnswerSet,
            dry_run: bool = True,
            ready_check: Any = None,
        ) -> Any:
            raise AssertionError("fill_and_submit não deveria ser chamado")

    settings = Settings(
        LLM_API_KEY="sk-test",  # type: ignore[arg-type]
        LLM_BASE_URL="https://api.example.com/v1",
        LLM_MODEL="gpt-test",
        DRIVE_FOLDER_ID="drv",
        LAYA_VISUAL_WATCH=True,
        LAYA_VISUAL_DELAY_MS=0,
        LAYA_EVAL_FILE=tmp_path / "eval.json",
        DECISION_AUDIT_FILE=tmp_path / "audit.jsonl",
    )
    register_handler(_ExpiredInhireHandler())
    try:
        pipeline = Pipeline(
            settings=settings,
            decision_layer=DecisionLayer(model=None, settings=settings),
            resume_resolver=_DummyResolver(),  # type: ignore[arg-type]
            browser_manager=_DummyBrowserMgr(),  # type: ignore[arg-type]
            matcher=_DummyMatcher(),  # type: ignore[arg-type]
            on_laya_event=events.append,
        )
        res = await pipeline.process_job(
            Job(
                id="inhire-expired",
                company="Paytrack",
                title="Dev Python",
                url="https://trinca.inhire.app/vagas/expirada",
                ats="Inhire",
                fit_score=93,
            ),
            Profile(
                full_name="Ana",
                email="ana@example.com",
                phone="11999990000",
                location="SP",
            ),
        )
        assert res.status == TaskStatus.SKIPPED
        titles = [e["title"] for e in events]
        assert "NAVEGAÇÃO · PÁGINA ABERTA" in titles
        assert "VAGA INDISPONÍVEL · SKIPPED" in titles
        last_ev = events[-1]
        assert last_ev["title"] == "VAGA INDISPONÍVEL · SKIPPED"
        assert "encerrada" in last_ev["detail"].lower()
        assert last_ev["frame_bytes"] == b"\xff\xd8\xffFAKEJPEG"
    finally:
        reset_registry()


