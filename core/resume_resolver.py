"""Resolve o rótulo de currículo da planilha para um arquivo sincronizado do Drive."""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from core.models import Job, ResumeInfo

log = logging.getLogger(__name__)

_PREFIXES: tuple[tuple[str, str], ...] = (
    ("curriculo otimizado", "pt"),
    ("resume optimized", "en"),
)
_EXTENSIONS = (".pdf", ".docx", ".doc")
_INDEXABLE = (".pdf",)


@dataclass
class ResolveResult:
    """`found` preenchido só quando há um único candidato confiável."""

    found: ResumeInfo | None
    candidates: list[str] = field(default_factory=list)
    reason: str = ""
    filename: str | None = None


def _strip_extension(name: str) -> str:
    lower = name.lower()
    for ext in _EXTENSIONS:
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def normalize(value: str) -> str:
    """Minúsculas, sem acentos/pontuação; '/' e '&' viram espaço."""
    text = _strip_extension(PurePath(value).name) if "/" not in value else _strip_extension(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("/", " ").replace("&", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_prefix(value: str) -> tuple[str, str | None]:
    """Remove o prefixo 'currículo otimizado'/'resume optimized'. Retorna (núcleo, idioma)."""
    norm = normalize(value)
    for prefix, lang in _PREFIXES:
        if norm.startswith(prefix):
            return norm[len(prefix):].strip(), lang
    return norm, None


def is_optimized_name(filename: str) -> bool:
    """True se o arquivo é um currículo otimizado (tem um dos prefixos)."""
    return split_prefix(filename)[1] is not None


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def resolve_label(
    label: str | None, resumes: dict[str, ResumeInfo], threshold: float
) -> ResolveResult:
    """Casa um rótulo com os arquivos: igualdade normalizada → difflib ≥ threshold."""
    if not label:
        return ResolveResult(found=None, reason="rótulo de currículo ausente na planilha")
    core, lang = split_prefix(label)
    keyed = {name: split_prefix(name) for name in resumes}

    exact = [name for name, (fcore, _) in keyed.items() if fcore == core]
    if len(exact) > 1 and lang is not None:
        same_lang = [name for name in exact if keyed[name][1] == lang]
        exact = same_lang or exact
    if len(exact) == 1:
        return ResolveResult(found=resumes[exact[0]], reason="igualdade normalizada", filename=exact[0])
    if len(exact) > 1:
        return ResolveResult(found=None, candidates=sorted(exact), reason="ambíguo: múltiplos iguais")

    scored = sorted(
        ((_ratio(core, fcore), name) for name, (fcore, flang) in keyed.items()
         if lang is None or flang in (None, lang)),
        reverse=True,
    )
    above = [(score, name) for score, name in scored if score >= threshold]
    if len(above) == 1:
        name = above[0][1]
        return ResolveResult(found=resumes[name], reason=f"similaridade {above[0][0]:.2f}", filename=name)
    if len(above) > 1:
        return ResolveResult(
            found=None, candidates=[name for _, name in above], reason="ambíguo: múltiplos similares"
        )
    near = [name for score, name in scored[:3] if score >= threshold * 0.7]
    return ResolveResult(found=None, candidates=near, reason="currículo não encontrado")


def resolve(job: Job, resumes: dict[str, ResumeInfo], threshold: float = 0.9) -> ResolveResult:
    """Resolve o currículo otimizado indicado na planilha para a vaga."""
    return resolve_label(job.resume_label, resumes, threshold)


def resolve_bases(
    base_names: list[str], resumes: dict[str, ResumeInfo], threshold: float = 0.9
) -> dict[str, ResumeInfo]:
    """Resolve currículos-base (ex.: 'Cognition', 'Base Inglês') ignorando os otimizados."""
    bases = {name: info for name, info in resumes.items() if not is_optimized_name(name)}
    out: dict[str, ResumeInfo] = {}
    for base in base_names:
        target = normalize(base)
        if not target:
            continue
        tokens = set(target.split())
        contained = [name for name in bases if tokens <= set(normalize(name).split())]
        if len(contained) == 1:
            out[base] = bases[contained[0]]
            continue
        pool = contained or list(bases)
        scored = sorted(((_ratio(target, normalize(name)), name) for name in pool), reverse=True)
        if scored and scored[0][0] >= threshold and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            out[base] = bases[scored[0][1]]
        elif contained:
            shortest = sorted(contained, key=lambda n: len(normalize(n)))
            if len(shortest) == 1 or len(normalize(shortest[0])) < len(normalize(shortest[1])):
                out[base] = bases[shortest[0]]
    return out


class ResumeResolver:
    """Classe de serviço para carregar currículos em cache e resolver para vagas."""

    def __init__(
        self,
        cache_dir: PurePath | Path | None = None,
        threshold: float = 0.9,
        loaded_resumes: dict[str, ResumeInfo] | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.threshold = threshold
        self._resumes: dict[str, ResumeInfo] = dict(loaded_resumes) if loaded_resumes else {}

    def set_resumes(self, resumes: dict[str, ResumeInfo]) -> None:
        self._resumes = dict(resumes)

    def get_resumes(self) -> dict[str, ResumeInfo]:
        return dict(self._resumes)

    def get_base_resumes(self, base_names: list[str] | None = None) -> dict[str, ResumeInfo]:
        """Retorna currículos-base elegíveis para fallback, nunca currículos otimizados de outras vagas."""
        if base_names:
            resolved = resolve_bases(base_names, self._resumes, self.threshold)
            if resolved:
                return {info.path.name: info for info in resolved.values()}
        return {n: i for n, i in self._resumes.items() if not is_optimized_name(n)}

    def load_cache(self, extract_fn: Callable[[Path], ResumeInfo] | None = None) -> None:
        """Lê os arquivos do diretório de cache e popula a tabela de currículos."""
        if not self.cache_dir or not self.cache_dir.exists():
            return

        from core.pdf_text import build_resume_summary, extract_pdf_text

        for path in self.cache_dir.iterdir():
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in _INDEXABLE:
                if suffix in _EXTENSIONS:
                    log.info(
                        "Arquivo '%s' ignorado: extensão '%s' não suportada para indexação direta",
                        path.name,
                        suffix,
                    )
                continue
            try:
                if extract_fn:
                    info = extract_fn(path)
                else:
                    text = extract_pdf_text(path)
                    summary = build_resume_summary(text)
                    info = ResumeInfo(path=path, summary=summary, text=text)
                self._resumes[path.name] = info
            except Exception as exc:
                log.warning("Erro ao indexar currículo '%s': %s", path.name, exc)

    async def resolve(self, job: Job) -> ResumeInfo | None:
        """Resolve o currículo para a vaga informada."""
        result = resolve(job, self._resumes, self.threshold)
        return result.found
