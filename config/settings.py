"""Configuração via `pydantic-settings` (env/.env). Precedência: CLI > env > default."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DPMode = Literal["off", "shadow", "active"]
DP_NAMES: tuple[str, ...] = ("DP1", "DP2", "DP3", "DP4", "DP5", "DP6")
#: DPs que podem liberar uma ação: só ficam `active` com avaliação aprovada.
RELEASING_DPS: frozenset[str] = frozenset({"DP1", "DP2", "DP5"})

_DEFAULT_DP_MODES: dict[str, DPMode] = {
    "DP1": "shadow",
    "DP2": "shadow",
    "DP3": "active",
    "DP4": "active",
    "DP5": "shadow",
    "DP6": "active",
}

_DEFAULT_BLOCKED = [
    "*google-analytics.com*",
    "*googletagmanager.com*",
    "*doubleclick.net*",
    "*facebook.net*",
    "*hotjar.com*",
    "*segment.io*",
]


class Settings(BaseSettings):
    """Configuração do harness. Segredos como `SecretStr` (nunca aparecem em repr/log)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- obrigatórios ---
    LLM_API_KEY: SecretStr
    LLM_BASE_URL: str
    LLM_MODEL: str
    DRIVE_FOLDER_ID: str

    # --- execução ---
    GOOGLE_SHEET_URL: str | None = None
    GOOGLE_OAUTH_CLIENT_FILE: Path | None = Path("./client_secret.json")
    GOOGLE_SERVICE_ACCOUNT_FILE: Path = Path("./service_account.json")
    GOOGLE_TOKEN_FILE: Path = Path("./token.json")
    INCLUDE_BLANK_STATUS: bool = True
    MAX_CONCURRENCY: int = 3
    DRY_RUN: bool = True
    HEADLESS: bool = True
    USE_CAMOUFOX: bool = True
    AUTONOMOUS_MODE: bool = False
    STORAGE_STATE_PATH: Path = Path("./cache/storage_state.json")
    CACHE_DIR: Path = Path("./cache/resumes")
    SCREENSHOT_DIR: Path = Path("./artifacts/screenshots")
    RESULTS_FILE: Path = Path("./artifacts/results.jsonl")
    SHEET_RESULT_FILE: Path = Path("./artifacts/planilha_atualizada.xlsx")
    TASK_TIMEOUT_S: float = 120
    NAV_TIMEOUT_MS: int = 30000
    ACTION_TIMEOUT_MS: int = 10000
    MAX_RETRIES: int = 2
    BACKOFF_BASE_S: float = 1.0
    BLOCKED_URL_PATTERNS: list[str] = Field(default_factory=lambda: list(_DEFAULT_BLOCKED))

    # --- LLM ---
    NO_LLM: bool = False
    LLM_MAX_RETRIES: int = 3
    LLM_TIMEOUT_S: float = 60
    LLM_MAX_INPUT_CHARS: int = 12000

    # --- planilha e currículo ---
    MIN_FIT_SCORE: int = 70
    FIT_GATE_ENABLED: bool = True
    RESUME_MATCH_THRESHOLD: float = 0.9

    # --- decisão ---
    DECISION_BACKEND: Literal["laya", "rules"] = "laya"
    LAYA_URL: str = "http://127.0.0.1:8787"
    LAYA_TIMEOUT_S: float = 5.0
    LAYA_CPU_TIMEOUT_FACTOR: float = 5
    LAYA_MAX_FAILURES: int = 3
    LAYA_BREAKER_COOLDOWN_S: float = 60.0
    LAYA_YES_THRESHOLD: float = 0.8
    LAYA_NO_THRESHOLD: float = 0.2
    LAYA_MIN_MARGIN: float = 0.15
    LAYA_ORDER_SWAP: bool = True
    LAYA_STATE_MAX_CHARS: int = 1500
    LAYA_PLAN_ABOVE_CHARS: int = 600
    LAYA_DP_MODES: dict[str, DPMode] = Field(default_factory=lambda: dict(_DEFAULT_DP_MODES))
    LAYA_REQUIRE_EVAL_FOR_ACTIVE: bool = True
    LAYA_EVAL_FILE: Path = Path("./artifacts/laya_eval.json")
    LAYA_EVAL_MAX_AGE_DAYS: int = 14
    EVAL_MIN_EXAMPLES: int = 30
    EVAL_MARGIN: float = 0.10
    EVAL_MIN_COVERAGE: float = 0.5
    DECISION_AUDIT_FILE: Path = Path("./artifacts/decisions.jsonl")

    @field_validator("MAX_CONCURRENCY")
    @classmethod
    def _concurrency_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("MAX_CONCURRENCY deve ser >= 1")
        return value

    @field_validator("LLM_MAX_RETRIES")
    @classmethod
    def _llm_retries_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("LLM_MAX_RETRIES deve ser >= 1")
        return value

    @field_validator("TASK_TIMEOUT_S")
    @classmethod
    def _task_timeout_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("TASK_TIMEOUT_S deve ser > 0")
        return value

    @field_validator("RESUME_MATCH_THRESHOLD")
    @classmethod
    def _resume_match_threshold_range(cls, value: float) -> float:
        if not 0 < value <= 1:
            raise ValueError("RESUME_MATCH_THRESHOLD deve estar em (0, 1]")
        return value

    @field_validator("MIN_FIT_SCORE")
    @classmethod
    def _fit_range(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("MIN_FIT_SCORE deve estar em [0, 100]")
        return value

    @field_validator("LAYA_MIN_MARGIN")
    @classmethod
    def _margin_range(cls, value: float) -> float:
        if not 0 < value < 1:
            raise ValueError("LAYA_MIN_MARGIN deve estar em (0, 1)")
        return value

    @field_validator("LAYA_DP_MODES")
    @classmethod
    def _complete_modes(cls, value: dict[str, DPMode]) -> dict[str, DPMode]:
        unknown = set(value) - set(DP_NAMES)
        if unknown:
            raise ValueError(f"DPs desconhecidos em LAYA_DP_MODES: {sorted(unknown)}")
        merged = dict(_DEFAULT_DP_MODES)
        merged.update(value)
        return merged

    @model_validator(mode="after")
    def _thresholds(self) -> Settings:
        if not 0 <= self.LAYA_NO_THRESHOLD < self.LAYA_YES_THRESHOLD <= 1:
            raise ValueError("exige 0 <= LAYA_NO_THRESHOLD < LAYA_YES_THRESHOLD <= 1")
        return self

    def ensure_dirs(self) -> None:
        """Cria sob demanda os diretórios de cache e artefatos."""
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        for file_path in (
            self.RESULTS_FILE,
            self.SHEET_RESULT_FILE,
            self.LAYA_EVAL_FILE,
            self.DECISION_AUDIT_FILE,
        ):
            file_path.parent.mkdir(parents=True, exist_ok=True)


def load_settings(**overrides: object) -> Settings:
    """Carrega Settings do ambiente/.env aplicando overrides (ex.: flags da CLI)."""
    clean = {key: value for key, value in overrides.items() if value is not None}
    return Settings(**clean)  # type: ignore[arg-type]
