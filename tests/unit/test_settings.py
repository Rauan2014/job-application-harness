"""Testes unitários de configuração (Settings)."""

import pytest
from pydantic import ValidationError

from config.settings import Settings, load_settings


def test_missing_required_raises() -> None:
    """Cada variável obrigatória ausente deve levantar ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    errors = exc_info.value.errors()
    missing_fields = {err["loc"][0] for err in errors}
    assert "llm_api_key" in missing_fields or "LLM_API_KEY" in missing_fields
    assert "llm_base_url" in missing_fields or "LLM_BASE_URL" in missing_fields
    assert "llm_model" in missing_fields or "LLM_MODEL" in missing_fields
    assert "drive_folder_id" in missing_fields or "DRIVE_FOLDER_ID" in missing_fields


def test_defaults(test_settings: Settings) -> None:
    """Verifica todos os valores padrão inegociáveis."""
    assert test_settings.DRY_RUN is True
    assert test_settings.MAX_CONCURRENCY == 3
    assert test_settings.DECISION_BACKEND == "laya"
    assert test_settings.MIN_FIT_SCORE == 70
    assert test_settings.FIT_GATE_ENABLED is True
    assert test_settings.LAYA_DP_MODES["DP1"] == "shadow"
    assert test_settings.LAYA_DP_MODES["DP2"] == "shadow"
    assert test_settings.LAYA_DP_MODES["DP3"] == "active"
    assert test_settings.LAYA_DP_MODES["DP4"] == "active"
    assert test_settings.LAYA_DP_MODES["DP5"] == "shadow"
    assert test_settings.LAYA_DP_MODES["DP6"] == "active"
    assert test_settings.USE_CAMOUFOX is False


def test_invalid_concurrency_rejected() -> None:
    """MAX_CONCURRENCY < 1 deve ser rejeitado."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            LLM_API_KEY="k",
            LLM_BASE_URL="http://u",
            LLM_MODEL="m",
            DRIVE_FOLDER_ID="f",
            MAX_CONCURRENCY=0,
        )


def test_invalid_thresholds_rejected() -> None:
    """Limiares incoerentes (NO >= YES) devem ser rejeitados."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            LLM_API_KEY="k",
            LLM_BASE_URL="http://u",
            LLM_MODEL="m",
            DRIVE_FOLDER_ID="f",
            LAYA_NO_THRESHOLD=0.8,
            LAYA_YES_THRESHOLD=0.2,
        )


def test_invalid_dp_mode_rejected() -> None:
    """Modo inválido de DP deve ser rejeitado."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            LLM_API_KEY="k",
            LLM_BASE_URL="http://u",
            LLM_MODEL="m",
            DRIVE_FOLDER_ID="f",
            LAYA_DP_MODES={"DP1": "invalid_mode"},  # type: ignore[dict-item]
        )


def test_secret_not_in_repr(test_settings: Settings) -> None:
    """Segredos não devem aparecer no repr do objeto."""
    assert "test-secret-key" not in repr(test_settings)
    assert test_settings.LLM_API_KEY.get_secret_value() == "test-secret-key"


def test_load_settings_overrides() -> None:
    """load_settings deve aplicar overrides da CLI com precedência."""
    s = load_settings(
        LLM_API_KEY="override-key",
        LLM_BASE_URL="http://override",
        LLM_MODEL="model-override",
        DRIVE_FOLDER_ID="folder-override",
        DRY_RUN=False,
        MAX_CONCURRENCY=5,
    )
    assert s.DRY_RUN is False
    assert s.MAX_CONCURRENCY == 5
    assert s.LLM_MODEL == "model-override"


def test_camoufox_default_and_override() -> None:
    """Verifica default do Camoufox e override via load_settings."""
    s_default = load_settings(
        LLM_API_KEY="k",
        LLM_BASE_URL="http://u",
        LLM_MODEL="m",
        DRIVE_FOLDER_ID="f",
    )
    assert s_default.USE_CAMOUFOX is True

    s_custom = load_settings(
        LLM_API_KEY="k",
        LLM_BASE_URL="http://u",
        LLM_MODEL="m",
        DRIVE_FOLDER_ID="f",
        USE_CAMOUFOX=False,
    )
    assert s_custom.USE_CAMOUFOX is False


def test_no_llm_setting_and_override() -> None:
    """Verifica flag NO_LLM padrão e override via load_settings."""
    s_default = load_settings(
        LLM_API_KEY="k",
        LLM_BASE_URL="http://u",
        LLM_MODEL="m",
        DRIVE_FOLDER_ID="f",
    )
    assert s_default.NO_LLM is False

    s_no_llm = load_settings(
        LLM_API_KEY="k",
        LLM_BASE_URL="http://u",
        LLM_MODEL="m",
        DRIVE_FOLDER_ID="f",
        NO_LLM=True,
    )
    assert s_no_llm.NO_LLM is True


def test_include_blank_status_setting_and_override() -> None:
    """Verifica flag INCLUDE_BLANK_STATUS padrão (True) e override via load_settings."""
    s_default = load_settings(
        LLM_API_KEY="k",
        LLM_BASE_URL="http://u",
        LLM_MODEL="m",
        DRIVE_FOLDER_ID="f",
    )
    assert s_default.INCLUDE_BLANK_STATUS is True

    s_disabled = load_settings(
        LLM_API_KEY="k",
        LLM_BASE_URL="http://u",
        LLM_MODEL="m",
        DRIVE_FOLDER_ID="f",
        INCLUDE_BLANK_STATUS=False,
    )
    assert s_disabled.INCLUDE_BLANK_STATUS is False
