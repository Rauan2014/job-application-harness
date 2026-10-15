"""Configurações globais e fixtures base para a suíte de testes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from config.settings import Settings
from core.decision.models import ChoiceVerdict, NoulVerdict


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Aplica markers unit ou integration automaticamente pelo caminho do arquivo."""
    for item in items:
        fspath_str = str(item.fspath).replace("\\", "/")
        if "/tests/unit/" in fspath_str:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in fspath_str:
            item.add_marker(pytest.mark.integration)


# Bytes de um PDF mínimo válido com texto extraível "John Doe - Senior AI Engineer Resume"
_MINIMAL_VALID_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
    b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    b"5 0 obj << /Length 55 >> stream\n"
    b"BT\n"
    b"/F1 12 Tf\n"
    b"72 712 Td\n"
    b"(John Doe - Senior AI Engineer Resume) Tj\n"
    b"ET\n"
    b"endstream endobj\n"
    b"xref\n"
    b"0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000227 00000 n \n"
    b"0000000295 00000 n \n"
    b"trailer << /Size 6 /Root 1 0 R >>\n"
    b"startxref\n"
    b"401\n"
    b"%%EOF\n"
)


@pytest.fixture
def sample_resume_pdf(tmp_path: Path) -> Path:
    """Cria um PDF mínimo válido com texto em tmp_path."""
    pdf_path = tmp_path / "sample_resume.pdf"
    pdf_path.write_bytes(_MINIMAL_VALID_PDF_BYTES)
    return pdf_path


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Settings isoladas para testes unitários sem leitura de .env real."""
    return Settings(
        _env_file=None,
        LLM_API_KEY=SecretStr("test-secret-key"),
        LLM_BASE_URL="http://test.llm.local/v1",
        LLM_MODEL="test-model",
        DRIVE_FOLDER_ID="test-drive-folder-id",
        GOOGLE_SERVICE_ACCOUNT_FILE=tmp_path / "sa.json",
        CACHE_DIR=tmp_path / "cache",
        SCREENSHOT_DIR=tmp_path / "screenshots",
        RESULTS_FILE=tmp_path / "results.jsonl",
        SHEET_RESULT_FILE=tmp_path / "planilha_atualizada.xlsx",
        LAYA_EVAL_FILE=tmp_path / "laya_eval.json",
        DECISION_AUDIT_FILE=tmp_path / "decisions.jsonl",
        DRY_RUN=True,
        HEADLESS=True,
        USE_CAMOUFOX=False,
        STORAGE_STATE_PATH=tmp_path / "storage_state.json",
    )


class FakeDecisionModel:
    """Implementação controlada de DecisionModel para testes determinísticos."""

    def __init__(self) -> None:
        self.noul_verdicts: list[NoulVerdict] = []
        self.choice_verdicts: list[ChoiceVerdict] = []
        self.calls: list[dict[str, Any]] = []

    async def noul(
        self,
        state: dict[str, str],
        instructions: str,
        criteria: dict[str, str] | None = None,
    ) -> NoulVerdict:
        self.calls.append(
            {"type": "noul", "state": state, "instructions": instructions, "criteria": criteria}
        )
        if self.noul_verdicts:
            return self.noul_verdicts.pop(0)
        norm_inst = instructions.lower()
        if "bloquead" in norm_inst or "captcha" in norm_inst:
            return NoulVerdict(p_true=0.05, band="no")
        if "sensíve" in norm_inst or "sensitive" in norm_inst or "eeo" in norm_inst:
            return NoulVerdict(p_true=0.05, band="no")
        return NoulVerdict(p_true=0.9, band="yes")

    async def choice(
        self,
        state: dict[str, str],
        instructions: str,
        options: dict[str, str],
    ) -> ChoiceVerdict:
        self.calls.append(
            {"type": "choice", "state": state, "instructions": instructions, "options": options}
        )
        if self.choice_verdicts:
            return self.choice_verdicts.pop(0)
        top = next(iter(options)) if options else "none"
        return ChoiceVerdict(
            probs={opt: (1.0 if opt == top else 0.0) for opt in options},
            top=top,
            margin=1.0,
            order_consistent=True,
        )


@pytest.fixture
def fake_decision_model() -> FakeDecisionModel:
    return FakeDecisionModel()
