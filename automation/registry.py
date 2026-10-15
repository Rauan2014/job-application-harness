"""Registro central de handlers de ATS suportados pelo harness."""

from __future__ import annotations

import logging

from automation.base_handler import BaseATSHandler
from automation.handlers.aggregator import AggregatorHandler
from automation.handlers.greenhouse import GreenhouseHandler
from automation.handlers.indeed import IndeedApplyHandler
from automation.handlers.lever import LeverHandler
from automation.handlers.workable import WorkableHandler
from core.models import Job

logger = logging.getLogger(__name__)

_DEFAULT_HANDLERS: list[BaseATSHandler] = [
    LeverHandler(),
    GreenhouseHandler(),
    WorkableHandler(),
    IndeedApplyHandler(),
    AggregatorHandler(),
]

_REGISTRY: list[BaseATSHandler] = list(_DEFAULT_HANDLERS)


def register_handler(handler: BaseATSHandler) -> None:
    """Registra um novo handler ou substitui existente com mesmo nome."""
    global _REGISTRY
    _REGISTRY = [h for h in _REGISTRY if h.name != handler.name]
    _REGISTRY.append(handler)


def reset_registry() -> None:
    """Restaura o registro padrão de handlers."""
    global _REGISTRY
    _REGISTRY = list(_DEFAULT_HANDLERS)


def get_handler(job: Job) -> BaseATSHandler | None:
    """Retorna o handler apropriado para a vaga ou None se a ATS não for suportada."""
    for handler in _REGISTRY:
        if handler.can_handle(job):
            return handler
    logger.warning("Nenhum handler de ATS encontrado para job '%s' (ats=%s, url=%s)", job.id, job.ats, job.url)
    return None
