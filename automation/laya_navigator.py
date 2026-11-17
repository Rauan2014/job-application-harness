"""Navegador web genérico guiado pela Laya (System 1) com escalonamento para LLM/regras (System 2)."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from automation.base_handler import BaseATSHandler
from core.decision.laya_client import DecisionModel
from core.decision.rules import (
    _normalize,
    has_blocker_marker,
    is_sensitive_question,
    match_option,
    rank_profile_keys,
    resolve_profile_answer,
)
from core.models import (
    AnswerSet,
    ApplicationResult,
    BlockerDetected,
    FormQuestion,
    Job,
    PageInfo,
    Profile,
    ResumeInfo,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

    from config.settings import Settings
    from core.matcher import Matcher

logger = logging.getLogger(__name__)

_HUD_INIT_JS = """
(engineText) => {
  if (!document.getElementById('laya-hud')) {
    const style = document.createElement('style');
    style.id = 'laya-hud-style';
    style.textContent = `
      #laya-hud {
        position: fixed; top: 12px; right: 12px; width: 348px; max-height: calc(100vh - 24px);
        z-index: 2147483646; background: rgba(2, 6, 23, 0.94); color: #e2e8f0;
        border: 1px solid #0ea5e9; border-radius: 12px; overflow: hidden;
        display: flex; flex-direction: column; font-family: 'Segoe UI', system-ui, sans-serif;
        box-shadow: 0 12px 34px rgba(0, 0, 0, 0.58); backdrop-filter: blur(8px);
      }
      #laya-hud.lh-collapsed { width: auto; height: auto; }
      #laya-hud.lh-collapsed #laya-hud-stats,
      #laya-hud.lh-collapsed #laya-hud-log,
      #laya-hud.lh-collapsed #laya-hud-engine { display: none; }
      #laya-hud-head {
        padding: 10px 12px; background: #0f172a; border-bottom: 1px solid #1e293b;
        display: flex; align-items: flex-start; justify-content: space-between; gap: 8px;
      }
      #laya-hud-engine { font-size: 11px; font-weight: 700; margin-top: 3px; color: #38bdf8; }
      #laya-hud-toggle {
        background: #1e293b; color: #94a3b8; border: 1px solid #334155; border-radius: 6px;
        font-size: 11px; padding: 2px 7px; cursor: pointer; flex-shrink: 0;
      }
      #laya-hud-stats {
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px;
        padding: 8px 10px; background: #0b1120; border-bottom: 1px solid #1e293b; text-align: center;
      }
      .lh-stat { background: #1e293b; padding: 5px; border-radius: 6px; font-size: 10px; color: #cbd5e1; }
      .lh-stat strong { display: block; font-size: 15px; color: #f8fafc; }
      #laya-hud-log {
        flex: 1; max-height: 360px; overflow-y: auto; padding: 10px;
        display: flex; flex-direction: column; gap: 6px;
      }
      .lh-entry {
        background: #0f172a; border-left: 3px solid #38bdf8; padding: 7px 9px;
        border-radius: 6px; font-size: 11px; line-height: 1.35; word-break: break-word;
      }
      .lh-entry.llm { border-left-color: #f59e0b; }
      .lh-entry.eeo { border-left-color: #a855f7; }
      .lh-entry.action { border-left-color: #22c55e; }
      .lh-badge {
        display: inline-block; font-size: 9px; font-weight: 700; text-transform: uppercase;
        padding: 1px 5px; border-radius: 4px; margin-bottom: 3px; background: #1e293b;
      }
      #laya-spotlight-tip {
        position: fixed; z-index: 2147483647; background: #0ea5e9; color: #020617;
        font-weight: 700; font-size: 11px; padding: 4px 9px; border-radius: 6px;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.48); pointer-events: none; max-width: 300px;
      }
      .laya-hl-laya { outline: 3px solid #0ea5e9 !important; outline-offset: 3px !important; }
      .laya-hl-llm { outline: 3px solid #f59e0b !important; outline-offset: 3px !important; }
      .laya-hl-eeo { outline: 3px solid #a855f7 !important; outline-offset: 3px !important; }
      .laya-hl-action { outline: 3px solid #22c55e !important; outline-offset: 3px !important; }
    `;
    document.head.appendChild(style);

    const hud = document.createElement('aside');
    hud.id = 'laya-hud';
    hud.innerHTML = `
      <div id="laya-hud-head">
        <div>
          <div style="font-size:13px;font-weight:700;">Laya Web Navigator · Ao Vivo</div>
          <div id="laya-hud-engine"></div>
        </div>
        <button id="laya-hud-toggle" type="button" title="Recolher/Expandir HUD">▾</button>
      </div>
      <div id="laya-hud-stats">
        <div class="lh-stat"><strong id="lh-s1">0</strong>System 1 (Laya)</div>
        <div class="lh-stat"><strong id="lh-s2">0</strong>System 2 (LLM)</div>
        <div class="lh-stat"><strong id="lh-eeo">0</strong>Regras / EEO</div>
      </div>
      <div id="laya-hud-log"></div>
    `;
    document.body.appendChild(hud);
    const toggleBtn = document.getElementById('laya-hud-toggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => {
        const collapsed = hud.classList.toggle('lh-collapsed');
        toggleBtn.textContent = collapsed ? '▸' : '▾';
      });
    }
  }
  const engineEl = document.getElementById('laya-hud-engine');
  if (engineEl) engineEl.textContent = engineText;
}
"""

_HUD_EVENT_JS = """
({ title, detail, kind, s1, s2, eeo, selector, clearOnly }) => {
  document.querySelectorAll('.laya-hl-laya,.laya-hl-llm,.laya-hl-eeo,.laya-hl-action').forEach((n) => {
    n.classList.remove('laya-hl-laya', 'laya-hl-llm', 'laya-hl-eeo', 'laya-hl-action');
  });
  document.getElementById('laya-spotlight-tip')?.remove();
  if (clearOnly) return;

  const s1El = document.getElementById('lh-s1');
  const s2El = document.getElementById('lh-s2');
  const eeoEl = document.getElementById('lh-eeo');
  if (s1El) s1El.textContent = String(s1);
  if (s2El) s2El.textContent = String(s2);
  if (eeoEl) eeoEl.textContent = String(eeo);

  const log = document.getElementById('laya-hud-log');
  if (log) {
    const item = document.createElement('div');
    item.className = 'lh-entry ' + (kind || 'laya');
    const b = document.createElement('div');
    b.className = 'lh-badge';
    b.textContent = title;
    const d = document.createElement('div');
    d.textContent = detail;
    item.appendChild(b);
    item.appendChild(d);
    log.appendChild(item);
    while (log.children.length > 40) {
      log.removeChild(log.firstChild);
    }
    log.scrollTop = log.scrollHeight;
  }

  if (selector) {
    const el = document.querySelector(selector);
    if (el) {
      try {
        el.scrollIntoView({ behavior: 'instant', block: 'center' });
      } catch (_err) {
        el.scrollIntoView({ block: 'center' });
      }
      const cls =
        kind === 'llm' ? 'laya-hl-llm' :
        kind === 'eeo' ? 'laya-hl-eeo' :
        kind === 'action' ? 'laya-hl-action' : 'laya-hl-laya';
      el.classList.add(cls);
      const rect = el.getBoundingClientRect();
      const tip = document.createElement('div');
      tip.id = 'laya-spotlight-tip';
      const colors = { laya: '#0ea5e9', llm: '#f59e0b', eeo: '#a855f7', action: '#22c55e' };
      tip.style.background = colors[kind] || '#0ea5e9';
      tip.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 360)) + 'px';
      tip.style.top = Math.max(8, rect.top - 30) + 'px';
      tip.textContent = title;
      document.body.appendChild(tip);
    }
  }
}
"""


_EL_HIGHLIGHT_JS = """
(el, { title, kind }) => {
  if (!el || typeof el.getBoundingClientRect !== 'function') return;
  document.querySelectorAll('.laya-hl-laya,.laya-hl-llm,.laya-hl-eeo,.laya-hl-action').forEach((n) => {
    n.classList.remove('laya-hl-laya', 'laya-hl-llm', 'laya-hl-eeo', 'laya-hl-action');
  });
  document.getElementById('laya-spotlight-tip')?.remove();
  try {
    el.scrollIntoView({ behavior: 'instant', block: 'center' });
  } catch (_err) {
    el.scrollIntoView({ block: 'center' });
  }
  const cls =
    kind === 'llm' ? 'laya-hl-llm' :
    kind === 'eeo' ? 'laya-hl-eeo' :
    kind === 'action' ? 'laya-hl-action' : 'laya-hl-laya';
  el.classList.add(cls);
  const rect = el.getBoundingClientRect();
  const tip = document.createElement('div');
  tip.id = 'laya-spotlight-tip';
  const colors = { laya: '#0ea5e9', llm: '#f59e0b', eeo: '#a855f7', action: '#22c55e' };
  tip.style.background = colors[kind] || '#0ea5e9';
  tip.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 360)) + 'px';
  tip.style.top = Math.max(8, rect.top - 30) + 'px';
  tip.textContent = title;
  document.body.appendChild(tip);
}
"""


def _unwrap_page(page: Any) -> Any:
    """Retorna a instância original de Playwright `Page` caso esteja embrulhada por proxy visual."""
    while hasattr(page, "_page") and page._page is not None:
        page = page._page
    return page


class VisualLocatorProxy:
    """Intercepta ações em locators para destacar o alvo na tela e emitir telemetria ao vivo."""

    def __init__(
        self,
        inner_loc: Any,
        navigator: LayaWebNavigator,
        selector: str = "",
        label_hint: str = "",
    ) -> None:
        self._inner = inner_loc
        self._nav = navigator
        self._selector = selector
        self._label_hint = label_hint

    @property
    def first(self) -> VisualLocatorProxy:
        return VisualLocatorProxy(self._inner.first, self._nav, self._selector, self._label_hint)

    @property
    def last(self) -> VisualLocatorProxy:
        return VisualLocatorProxy(self._inner.last, self._nav, self._selector, self._label_hint)

    def nth(self, idx: int) -> VisualLocatorProxy:
        return VisualLocatorProxy(self._inner.nth(idx), self._nav, self._selector, self._label_hint)

    def locator(self, selector: str, *args: Any, **kwargs: Any) -> VisualLocatorProxy:
        return VisualLocatorProxy(
            self._inner.locator(selector, *args, **kwargs),
            self._nav,
            selector,
            self._label_hint,
        )

    async def _should_spotlight(self) -> bool:
        """Evita atrasos em sondagens invisíveis ou ausentes de handlers clássicos."""
        if hasattr(self._inner, "count") and hasattr(self._inner, "is_visible"):
            try:
                cnt = await self._inner.count()
                if cnt != 1:
                    return False
                return bool(await self._inner.is_visible())
            except Exception:
                return False
        return True

    async def _emit_action(self, title: str, detail: str) -> None:
        if not await self._should_spotlight():
            return
        await self._nav.emit_visual(
            title=title,
            detail=detail,
            kind="action",
            selector=self._selector or None,
            locator=self._inner,
        )

    async def click(self, *args: Any, **kwargs: Any) -> Any:
        await self._emit_action("AÇÃO · CLIQUE", "Executando clique no elemento identificado")
        return await self._inner.click(*args, **kwargs)

    async def dblclick(self, *args: Any, **kwargs: Any) -> Any:
        await self._emit_action("AÇÃO · DUPLO CLIQUE", "Executando duplo clique no elemento")
        return await self._inner.dblclick(*args, **kwargs)

    async def fill(self, value: str, *args: Any, **kwargs: Any) -> Any:
        preview = value[:45] + ("..." if len(value) > 45 else "")
        await self._emit_action("AÇÃO · PREENCHER", f'Digitando: "{preview}"')
        return await self._inner.fill(value, *args, **kwargs)

    async def select_option(self, *args: Any, **kwargs: Any) -> Any:
        chosen = kwargs.get("label") or kwargs.get("value") or str(args)
        await self._emit_action("AÇÃO · SELECT", f'Selecionando opção: "{chosen}"')
        return await self._inner.select_option(*args, **kwargs)

    async def check(self, *args: Any, **kwargs: Any) -> Any:
        await self._emit_action("AÇÃO · CHECK / RADIO", "Marcando opção verificada")
        return await self._inner.check(*args, **kwargs)

    async def uncheck(self, *args: Any, **kwargs: Any) -> Any:
        await self._emit_action("AÇÃO · UNCHECK", "Desmarcando opção")
        return await self._inner.uncheck(*args, **kwargs)

    async def set_input_files(self, files: Any, *args: Any, **kwargs: Any) -> Any:
        raw_item = files[0] if isinstance(files, (list, tuple)) and files else files
        file_name = str(raw_item).replace("\\", "/").rsplit("/", 1)[-1]
        await self._emit_action("AÇÃO · UPLOAD PDF", f"Anexando arquivo: {file_name}")
        return await self._inner.set_input_files(files, *args, **kwargs)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


class VisualPageProxy:
    """Envolve `Page` do Playwright mantendo o HUD visual e o streaming sincronizados."""

    def __init__(
        self,
        page: Page,
        navigator: LayaWebNavigator,
        *,
        intercept_all: bool = False,
    ) -> None:
        self._page = page
        self._nav = navigator
        self._intercept_all = intercept_all

    async def goto(self, url: str, *args: Any, **kwargs: Any) -> Any:
        response = await self._page.goto(url, *args, **kwargs)
        # Em SPAs React/Next.js (ex: Inhire, Ashby, Gupy), aguarda brevemente a hidratação inicial
        # para não capturar apenas skeletons cinzas no primeiro frame.
        if hasattr(self._page, "wait_for_function"):
            with contextlib.suppress(Exception):
                await self._page.wait_for_function(
                    """() => Boolean(
                        document.body && (
                            (document.body.innerText || '').trim().length > 100 ||
                            document.querySelectorAll('input, textarea, select, button').length > 3
                        )
                    )""",
                    timeout=3500,
                )
        await self._nav.emit_visual(
            title="NAVEGAÇÃO · PÁGINA ABERTA",
            detail=f"Portal carregado: {self._page.url or url}",
            kind="laya",
            pause=False,
        )
        return response

    def locator(self, selector: str, *args: Any, **kwargs: Any) -> Any:
        inner = self._page.locator(selector, *args, **kwargs)
        if self._intercept_all or "data-laya-id" in selector:
            return VisualLocatorProxy(inner, self._nav, selector=selector)
        return inner

    def _wrap_semantic_loc(self, inner: Any, hint: str) -> Any:
        return VisualLocatorProxy(inner, self._nav, label_hint=hint) if self._intercept_all else inner

    def get_by_role(self, role: Any, *args: Any, **kwargs: Any) -> Any:
        return self._wrap_semantic_loc(self._page.get_by_role(role, *args, **kwargs), str(role))

    def get_by_label(self, text: Any, *args: Any, **kwargs: Any) -> Any:
        return self._wrap_semantic_loc(self._page.get_by_label(text, *args, **kwargs), str(text))

    def get_by_text(self, text: Any, *args: Any, **kwargs: Any) -> Any:
        return self._wrap_semantic_loc(self._page.get_by_text(text, *args, **kwargs), str(text))

    def get_by_placeholder(self, text: Any, *args: Any, **kwargs: Any) -> Any:
        return self._wrap_semantic_loc(self._page.get_by_placeholder(text, *args, **kwargs), str(text))

    def get_by_test_id(self, test_id: Any, *args: Any, **kwargs: Any) -> Any:
        return self._wrap_semantic_loc(self._page.get_by_test_id(test_id, *args, **kwargs), str(test_id))

    def __getattr__(self, item: str) -> Any:
        return getattr(self._page, item)

_GENERIC_CONFIRM_SELECTOR = (
    "[data-qa='confirmation'], .application-confirmation, .confirmation-message, "
    ".success-message, [data-testid='application-confirmation'], [data-testid='confirmation']"
)

_CANONICAL_PROFILE_KEYS: dict[str, str] = {
    "full_name": "nome completo candidato",
    "first_name": "primeiro nome",
    "last_name": "sobrenome",
    "email": "endereco de email",
    "phone": "telefone ou celular",
    "location": "cidade estado ou pais",
    "linkedin": "perfil ou url linkedin",
    "github": "perfil ou url github",
    "portfolio": "site pessoal ou portfolio",
    "work_authorization": "autorizacao trabalho visto",
    "salary_expectation": "pretensao salarial salario",
    "headline": "cargo atual ou titulo profissional",
    "current_company": "empresa atual",
    "education_school": "instituicao ensino faculdade",
}

_FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "full_name": ("nome", "name", "chamar", "nome completo", "full name", "seu nome", "candidato"),
    "first_name": ("primeiro nome", "first name", "given name"),
    "last_name": ("sobrenome", "last name", "family name", "surname"),
    "email": ("email", "e-mail", "correio", "mail"),
    "phone": ("telefone", "celular", "whatsapp", "phone", "mobile", "contato", "fone"),
    "location": ("cidade", "estado", "localizacao", "endereco", "onde mora", "based", "city", "location", "address"),
    "linkedin": ("linkedin",),
    "github": ("github", "git"),
    "portfolio": ("portfolio", "website", "site pessoal", "personal site", "homepage", "blog"),
    "work_authorization": (
        "autorizad",
        "authorized",
        "authorization",
        "visto",
        "visa",
        "sponsorship",
        "patrocinio",
        "eligible to work",
        "permissao",
    ),
    "salary_expectation": ("salar", "remunerac", "compensation", "pretensao", "expectativa salarial", "pay"),
    "headline": ("titulo", "headline", "cargo atual", "current title", "role"),
    "current_company": ("empresa atual", "current company", "employer"),
    "education_school": ("faculdade", "universidade", "escola", "school", "university", "college"),
}

_SEARCH_NOISE_RE = re.compile(r"\b(search|buscar|pesquisar|filtrar|cookie|filter)\b", re.IGNORECASE)
_CTA_POSITIVE_RE = re.compile(
    r"\b(apply|start application|candidatar|inscrever|iniciar candidatura|easy apply|apply now|"
    r"aplicar|continuar candidatura|preencher)\b",
    re.IGNORECASE,
)
_NEXT_STEP_RE = re.compile(
    r"\b(next|continue|proceed|advance|avancar|avançar|proximo|próximo|proxima|próxima|continuar|seguinte|step 2)\b",
    re.IGNORECASE,
)
_SUBMIT_RE = re.compile(
    r"\b(submit|send application|finish|complete application|enviar candidatura|enviar inscricao|"
    r"finalizar|concluir|enviar)\b",
    re.IGNORECASE,
)
_NEGATIVE_CTA_RE = re.compile(
    r"\b(login|sign in|sign up|register|cookie|privacy|privacidade|voltar|back|cancel|cancelar|"
    r"fechar|close|home|menu|share|compartilhar|save job|salvar vaga)\b",
    re.IGNORECASE,
)
_FILE_SKIP_RE = re.compile(r"\b(photo|foto|avatar|image|imagem|picture|video|selfie)\b", re.IGNORECASE)
_CONSENT_RE = re.compile(
    r"\b(term|terms|termos|privacy|privacidade|lgpd|gdpr|agree|concordo|aceito|declaro|consent|acknowledge)\b",
    re.IGNORECASE,
)

_EXTRACT_DOM_JS = """
(maxNodes) => {
  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const cleanText = (txt) => (txt || '').replace(/\\s+/g, ' ').trim();

  const controlTextWithoutOwnValues = (container, targetEl) => {
    const clone = container.cloneNode(true);
    clone.querySelectorAll('input, select, textarea, button, script, style').forEach((n) => n.remove());
    const txt = cleanText(clone.textContent);
    return txt;
  };

  const modalRoot = Array.from(
    document.querySelectorAll('[role="dialog"], [aria-modal="true"], dialog[open], .modal.show, .modal.open')
  ).find((m) => isVisible(m));

  const inferRadioGroupLabel = (el) => {
    const fieldset = el.closest('fieldset');
    if (fieldset) {
      const legend = fieldset.querySelector('legend');
      if (legend) return cleanText(legend.innerText);
    }
    const groupBox =
      el.closest('[role="radiogroup"], [data-field], .field, .form-group, .question') || el.parentElement;
    if (groupBox) {
      const promptEl = groupBox.querySelector('label, p, span, strong, h3, h4, .label, [data-label]');
      if (promptEl && !promptEl.contains(el)) {
        return cleanText(promptEl.innerText);
      }
      const prev = groupBox.previousElementSibling;
      if (prev && prev.tagName !== 'INPUT' && prev.tagName !== 'SELECT') {
        const pt = cleanText(prev.innerText);
        if (pt && pt.length <= 120) return pt;
      }
    }
    return '';
  };

  const inferLabel = (el, tag, inputType) => {
    if (tag === 'button' || tag === 'a' || inputType === 'submit' || inputType === 'button') {
      const txt = cleanText(el.innerText || el.value || el.getAttribute('aria-label') || el.title);
      return txt.slice(0, 120);
    }

    if (inputType === 'radio') {
      const wrapLabel = el.closest('label');
      let optText = '';
      if (wrapLabel) {
        optText = controlTextWithoutOwnValues(wrapLabel, el);
      } else if (el.id) {
        const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (explicit) optText = cleanText(explicit.innerText);
      }
      if (!optText && el.nextSibling && el.nextSibling.nodeType === Node.TEXT_NODE) {
        optText = cleanText(el.nextSibling.textContent);
      }
      if (!optText && el.nextElementSibling && ['SPAN', 'LABEL'].includes(el.nextElementSibling.tagName)) {
        optText = cleanText(el.nextElementSibling.innerText);
      }
      if (!optText) optText = cleanText(el.value || el.getAttribute('aria-label'));
      const grp = inferRadioGroupLabel(el);
      return JSON.stringify({ group: grp.slice(0, 100), option: optText.slice(0, 60) });
    }

    if (el.id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (explicit) {
        const t = cleanText(explicit.innerText);
        if (t) return t.slice(0, 120);
      }
    }

    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const parts = labelledBy
        .split(/\\s+/)
        .map((id) => cleanText(document.getElementById(id)?.innerText))
        .filter(Boolean);
      if (parts.length > 0) return parts.join(' ').slice(0, 120);
    }

    const ariaLabel = cleanText(el.getAttribute('aria-label'));
    if (ariaLabel) return ariaLabel.slice(0, 120);

    const wrapLabel = el.closest('label');
    if (wrapLabel) {
      const t = controlTextWithoutOwnValues(wrapLabel, el);
      if (t) return t.slice(0, 120);
    }

    // Procura label ou texto descritivo no container imediato (funciona sem id/name/for)
    const fieldWrap = el.closest('[data-field], .field, .form-group, .question') || el.parentElement;
    if (fieldWrap) {
      const inputsInside = fieldWrap.querySelectorAll('input:not([type="hidden"]), select, textarea').length;
      if (inputsInside <= 2) {
        for (const child of Array.from(fieldWrap.children)) {
          if (child === el || child.contains(el)) continue;
          if (['LABEL', 'SPAN', 'P', 'DIV', 'STRONG', 'H3', 'H4'].includes(child.tagName)) {
            if (child.querySelector('input, select, textarea, button')) continue;
            const ct = cleanText(child.innerText);
            if (ct && ct.length <= 120) return ct;
          }
        }
        const stripped = controlTextWithoutOwnValues(fieldWrap, el);
        if (stripped && stripped.length <= 120) return stripped;
      }
    }

    const prev = el.previousElementSibling;
    if (prev && ['LABEL', 'SPAN', 'P', 'STRONG'].includes(prev.tagName)) {
      const pt = cleanText(prev.innerText);
      if (pt && pt.length <= 120) return pt;
    }

    return cleanText(el.getAttribute('placeholder') || el.name || el.id).slice(0, 120);
  };

  document.querySelectorAll('[data-laya-id]').forEach((n) => n.removeAttribute('data-laya-id'));

  const selector = [
    'input:not([type="hidden"])',
    'textarea',
    'select',
    'button',
    'a[href]',
    '[role="button"]',
    '[role="link"]'
  ].join(', ');

  const rawNodes = Array.from(document.querySelectorAll(selector));
  const seen = new Set();
  const captured = [];

  for (const el of rawNodes) {
    if (captured.length >= maxNodes) break;
    if (seen.has(el)) continue;
    seen.add(el);
    if (!isVisible(el)) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;

    const tag = el.tagName.toLowerCase();
    const inputType = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : '';
    const role = (el.getAttribute('role') || '').toLowerCase();
    const inModal = modalRoot ? modalRoot.contains(el) : false;

    // Se existe um modal aberto, ignora botões/links de fundo fora do modal
    if (modalRoot && !inModal && (tag === 'a' || tag === 'button' || role === 'button' || role === 'link')) {
      continue;
    }

    const rawLabel = inferLabel(el, tag, inputType);
    let label = rawLabel;
    let options = [];
    let groupName = el.getAttribute('name') || '';

    if (inputType === 'radio') {
      try {
        const parsed = JSON.parse(rawLabel);
        label = parsed.group || groupName || 'radio';
        if (parsed.option) options = [parsed.option];
      } catch (_) {
        label = rawLabel;
      }
      if (!groupName) {
        const fs = el.closest('fieldset, [role="radiogroup"]');
        groupName = fs ? (fs.getAttribute('data-laya-grp') || (() => {
          const id = 'rg_' + captured.length;
          fs.setAttribute('data-laya-grp', id);
          return id;
        })()) : ('rg_' + label.slice(0, 20));
      }
    } else if (tag === 'select') {
      options = Array.from(el.options || [])
        .map((o) => cleanText(o.textContent))
        .filter((t) => t && !/^select\\b|^selecione\\b|^escolha\\b|^--/i.test(t))
        .slice(0, 30);
    }

    const required = Boolean(
      el.required ||
      el.getAttribute('aria-required') === 'true' ||
      /\\*/.test(label)
    );

    const eid = 'e' + captured.length;
    el.setAttribute('data-laya-id', eid);

    captured.push({
      eid,
      tag,
      input_type: inputType,
      role,
      label: label.slice(0, 120),
      name: el.getAttribute('name') || el.id || '',
      group_name: groupName,
      required,
      value: tag === 'select' || tag === 'input' || tag === 'textarea' ? String(el.value || '').slice(0, 80) : '',
      checked: Boolean(el.checked),
      options,
      in_modal: inModal,
      accept: el.getAttribute('accept') || ''
    });
  }

  const mainOrBody = document.querySelector('main')?.innerText || document.body?.innerText || '';
  const textSource = modalRoot ? modalRoot.innerText : mainOrBody;
  return {
    url: window.location.href,
    title: cleanText(document.title).slice(0, 120),
    has_modal: Boolean(modalRoot),
    body_excerpt: cleanText(textSource).slice(0, 400),
    elements: captured
  };
}
"""


class WebElementNode(BaseModel):
    """Representação semântica enxuta de um elemento interativo da página."""

    eid: str
    tag: str
    input_type: str = ""
    role: str = ""
    label: str = ""
    name: str = ""
    group_name: str = ""
    required: bool = False
    value: str = ""
    checked: bool = False
    options: list[str] = Field(default_factory=list)
    in_modal: bool = False
    accept: str = ""

    @property
    def selector(self) -> str:
        """Retorna o seletor temporário estável marcado no DOM pela extração semântica."""
        return f"[data-laya-id='{self.eid}']"

    @property
    def is_action(self) -> bool:
        """Indica se o elemento representa uma ação clicável (botão ou link)."""
        return (
            self.tag in ("button", "a")
            or self.input_type in ("submit", "button", "reset")
            or self.role in ("button", "link")
        )

    @property
    def is_form_field(self) -> bool:
        """Indica se o elemento é um campo de entrada de formulário."""
        return not self.is_action and self.tag in ("input", "textarea", "select")


class SemanticSnapshot(BaseModel):
    """Snapshot estruturado e compacto da página para decisão via Laya."""

    url: str
    title: str = ""
    has_modal: bool = False
    body_excerpt: str = ""
    elements: list[WebElementNode] = Field(default_factory=list)

    @property
    def form_inputs(self) -> list[WebElementNode]:
        """Retorna todos os campos de formulário visíveis (excluindo buscas simples)."""
        return [
            el
            for el in self.elements
            if el.is_form_field and el.input_type != "search" and not _SEARCH_NOISE_RE.search(el.label)
        ]

    @property
    def action_buttons(self) -> list[WebElementNode]:
        """Retorna botões e links clicáveis visíveis."""
        return [el for el in self.elements if el.is_action and el.label.strip()]


class NavigationStep(BaseModel):
    """Registro auditável de cada decisão de navegação/preenchimento."""

    step: int
    url: str
    action: str
    target_eid: str | None = None
    target_label: str = ""
    decided_by: str = "none"


class NavigatorStats(BaseModel):
    """Métricas de roteamento System 1 (Laya) vs System 2 (LLM) vs regras."""

    steps_taken: int = 0
    laya_decisions: int = 0
    llm_escalations: int = 0
    rule_fallbacks: int = 0
    history: list[NavigationStep] = Field(default_factory=list)


class _LLMChoiceResult(BaseModel):
    """Esquema JSON mínimo usado na escalação System 2 (Matcher)."""

    choice: str


def _clean_opt(text: str, max_len: int = 38) -> str:
    """Compacta texto de opção para caber no orçamento de tokens da Laya."""
    clean = re.sub(r"\s+", " ", text).strip()
    return clean if len(clean) <= max_len else f"{clean[: max_len - 3].rstrip()}..."


def _clean_label(label: str) -> str:
    """Remove asteriscos/marcadores finais de obrigatoriedade do rótulo."""
    return re.sub(r"\s*\*.*$", "", label).strip()


def _extract_profile_value(profile: Profile, canonical_key: str) -> str | None:
    """Extrai determinística e exatamente o valor correspondente do `Profile`."""
    if canonical_key == "full_name":
        return profile.full_name or None
    if canonical_key == "first_name":
        parts = profile.full_name.strip().split()
        return parts[0] if parts else None
    if canonical_key == "last_name":
        parts = profile.full_name.strip().split()
        return " ".join(parts[1:]) if len(parts) > 1 else (parts[0] if parts else None)
    if canonical_key == "email":
        return profile.email or None
    if canonical_key == "phone":
        return profile.phone or None
    if canonical_key == "location":
        return profile.location or None
    if canonical_key == "linkedin":
        return profile.links.get("linkedin") or next(
            (v for k, v in profile.links.items() if "linkedin" in k.lower()), None
        )
    if canonical_key == "github":
        return profile.links.get("github") or next(
            (v for k, v in profile.links.items() if "github" in k.lower()), None
        )
    if canonical_key == "portfolio":
        for key in ("portfolio", "website", "site", "blog"):
            if profile.links.get(key):
                return profile.links[key]
        return next(
            (v for k, v in profile.links.items() if "linkedin" not in k.lower() and "github" not in k.lower()),
            None,
        )
    if canonical_key == "work_authorization":
        return profile.work_authorization or None
    if canonical_key == "salary_expectation":
        return profile.salary_expectation or None
    if canonical_key == "headline":
        return profile.headline or None
    if canonical_key == "current_company":
        return profile.current_company or None
    if canonical_key == "education_school":
        return profile.education_school or None
    return None


def _rule_score_field_key(label: str, input_type: str) -> str | None:
    """Retorna a chave canônica de perfil caso haja correspondência direta forte."""
    if input_type == "email":
        return "email"
    if input_type == "tel":
        return "phone"

    norm = _normalize(label)
    for key, synonyms in _FIELD_SYNONYMS.items():
        if any(syn in norm for syn in synonyms):
            if key == "portfolio" and input_type == "url" and "linkedin" in norm:
                return "linkedin"
            if key == "portfolio" and input_type == "url" and "github" in norm:
                return "github"
            return key
    return None


def _score_action_candidate(
    node: WebElementNode,
    goal: Literal["open_form", "next_step", "submit"],
    visited_labels: set[str],
) -> float:
    """Pontua heuristicamente um botão/link antes do ranqueamento top-K para Laya."""
    lbl = node.label.strip()
    if not lbl:
        return -10.0

    norm = lbl.lower()
    if norm in visited_labels:
        return -5.0
    if _NEGATIVE_CTA_RE.search(norm) or _SEARCH_NOISE_RE.search(norm):
        return -4.0

    is_submit = bool(_SUBMIT_RE.search(norm)) or node.input_type == "submit"
    is_next = bool(_NEXT_STEP_RE.search(norm))
    is_cta = bool(_CTA_POSITIVE_RE.search(norm))

    score = 0.0
    if node.in_modal:
        score += 1.5
    if node.tag == "button" or node.role == "button":
        score += 0.5

    if goal == "open_form":
        if is_submit and not is_cta:
            return -3.0
        if is_cta:
            score += 4.0
        elif is_next:
            score += 2.0
    elif goal == "next_step":
        if is_next:
            score += 5.0
        if is_submit and not is_next:
            return -6.0
    elif goal == "submit":
        if is_submit:
            score += 5.0
        if node.input_type == "submit":
            score += 2.0
        if is_next and not is_submit:
            score -= 3.0

    return score


async def extract_semantic_snapshot(page: Page, max_nodes: int = 80) -> SemanticSnapshot:
    """Extrai snapshot semântico enxuto dos elementos interativos visíveis no DOM."""
    raw = await page.evaluate(_EXTRACT_DOM_JS, max_nodes)
    return SemanticSnapshot.model_validate(raw)


class LayaWebNavigator:
    """Orquestrador System 1 (Laya) -> System 2 (Matcher/regras) para navegação e formulários genéricos."""

    def __init__(
        self,
        decision_model: DecisionModel | None = None,
        matcher: Matcher | None = None,
        min_margin: float = 0.12,
        max_options: int = 8,
        max_steps: int = 8,
        visual_watch: bool = False,
        visual_delay_ms: int = 750,
        engine_label: str = "",
        on_visual_event: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.decision_model = decision_model
        self.matcher = matcher
        self.min_margin = float(min_margin)
        self.max_options = max(2, min(int(max_options), 10))
        self.max_steps = max(1, int(max_steps))
        self.visual_watch = bool(visual_watch)
        self.visual_delay_ms = max(0, int(visual_delay_ms))
        self.engine_label = engine_label
        self.on_visual_event = on_visual_event
        self.eeo_hits: int = 0
        self._active_raw_page: Any = None
        self.stats = NavigatorStats()
        self._dynamic_answer_cache: dict[str, str | None] = {}

    def reset_stats(self) -> None:
        """Reinicia contadores de decisão e histórico para uma nova vaga."""
        self.stats = NavigatorStats()
        self.eeo_hits = 0
        self._dynamic_answer_cache.clear()

    def wrap_page(self, page: Page, *, intercept_all: bool = False) -> Page:
        """Envolve `Page` com HUD visual ao vivo quando `visual_watch` estiver ativo."""
        raw_page = _unwrap_page(page)
        self._active_raw_page = raw_page
        if not self.visual_watch:
            return raw_page
        if isinstance(page, VisualPageProxy):
            if intercept_all and not page._intercept_all:
                return VisualPageProxy(raw_page, self, intercept_all=True)  # type: ignore[return-value]
            return page  # type: ignore[return-value]
        return VisualPageProxy(raw_page, self, intercept_all=intercept_all)  # type: ignore[return-value]

    def _resolve_engine_label(self) -> str:
        if self.engine_label:
            return self.engine_label
        has_laya = self.decision_model is not None
        has_llm = self.matcher is not None
        if has_laya and has_llm:
            return "Híbrido: System 1 (Laya) + System 2 (LLM)"
        if has_laya:
            return "System 1 (Laya)"
        if has_llm:
            return "Fallback System 2 (LLM)"
        return "Regras Determinísticas"

    async def _ensure_hud(self, raw_page: Any) -> None:
        if raw_page is None:
            return
        with contextlib.suppress(Exception):
            await raw_page.evaluate(_HUD_INIT_JS, self._resolve_engine_label())

    async def emit_visual(
        self,
        *,
        title: str,
        detail: str,
        kind: str = "laya",
        selector: str | None = None,
        locator: Any = None,
        pause: bool = True,
        capture_frame: bool = True,
    ) -> None:
        """Atualiza o HUD na página, captura frame JPEG compacto e notifica o painel Web/SSE."""
        if not self.visual_watch and self.on_visual_event is None:
            return

        raw_page = self._active_raw_page
        page_url = ""
        frame_bytes: bytes | None = None

        if self.visual_watch and raw_page is not None:
            await self._ensure_hud(raw_page)
            with contextlib.suppress(Exception):
                page_url = str(raw_page.url or "")
            has_dom_eval = locator is not None and hasattr(locator, "evaluate")
            with contextlib.suppress(Exception):
                await raw_page.evaluate(
                    _HUD_EVENT_JS,
                    {
                        "title": title,
                        "detail": detail,
                        "kind": kind,
                        "s1": self.stats.laya_decisions,
                        "s2": self.stats.llm_escalations,
                        "eeo": self.eeo_hits + self.stats.rule_fallbacks,
                        "selector": None if has_dom_eval else selector,
                        "clearOnly": False,
                    },
                )
            if has_dom_eval:
                with contextlib.suppress(Exception):
                    await locator.evaluate(_EL_HIGHLIGHT_JS, {"title": title, "kind": kind})
            if capture_frame:
                with contextlib.suppress(Exception):
                    frame_bytes = await raw_page.screenshot(type="jpeg", quality=62)

        if self.on_visual_event is not None:
            payload: dict[str, Any] = {
                "title": title,
                "detail": detail,
                "kind": kind,
                "s1": self.stats.laya_decisions,
                "s2": self.stats.llm_escalations,
                "eeo": self.eeo_hits + self.stats.rule_fallbacks,
                "url": page_url,
                "engine": self._resolve_engine_label(),
                "frame_bytes": frame_bytes,
            }
            with contextlib.suppress(Exception):
                res = self.on_visual_event(payload)
                if hasattr(res, "__await__"):
                    await res

        if pause and self.visual_watch and raw_page is not None and self.visual_delay_ms > 0:
            with contextlib.suppress(Exception):
                await raw_page.wait_for_timeout(self.visual_delay_ms)

    async def clear_visual_highlight(self) -> None:
        """Remove contorno/tooltip temporário na página atual."""
        if not self.visual_watch or self._active_raw_page is None:
            return
        with contextlib.suppress(Exception):
            await self._active_raw_page.evaluate(_HUD_EVENT_JS, {"clearOnly": True})

    def _can_escalate_llm(self) -> bool:
        """Verifica se o Matcher possui backend LLM utilizável sem violar NO_LLM."""
        if self.matcher is None:
            return False
        settings = getattr(self.matcher, "_settings", None)
        if settings is None:
            return True
        if getattr(settings, "NO_LLM", False):
            return False
        mode = getattr(settings, "LLM_MODE", "byok")
        api_key = getattr(settings, "LLM_API_KEY", None)
        if mode != "managed" and not api_key:
            # Permite apenas mocks/fakes que sobrescreveram _ask_json em testes unitários
            from core.matcher import Matcher as RealMatcher

            return type(self.matcher)._ask_json is not RealMatcher._ask_json
        return True

    async def _llm_pick_option(self, prompt_ctx: str, options: dict[str, str]) -> str | None:
        """Escala escolha ambígua para o System 2 (Matcher LLM) retornando a chave exata."""
        if not self._can_escalate_llm() or self.matcher is None:
            return None

        opts_lines = "\n".join(f"- {k}: {v}" for k, v in options.items())
        messages = [
            {
                "role": "system",
                "content": (
                    "Você escolhe a melhor opção entre chaves permitidas. "
                    'Responda apenas JSON válido: {"choice": "<chave>"}.'
                ),
            },
            {
                "role": "user",
                "content": f"{prompt_ctx}\nOpções válidas:\n{opts_lines}",
            },
        ]
        try:
            parsed = await self.matcher._ask_json(messages, _LLMChoiceResult.model_validate)
            choice_key = getattr(parsed, "choice", "")
            if choice_key in options:
                return str(choice_key)
        except Exception as exc:
            logger.debug("Escalação LLM (choice) falhou; aplicando regra determinística: %s", exc)
        return None

    async def _ask_choice(
        self,
        state: dict[str, str],
        instructions: str,
        options: dict[str, str],
        fallback_key: str,
    ) -> tuple[str, str]:
        """Executa decisão System 1 via Laya.choice com escalonamento System 2 em incerteza."""
        # Proteção contra colapso Banking77: garante no máximo max_options entradas
        if len(options) > self.max_options:
            trimmed = dict(list(options.items())[: self.max_options - 1])
            if "none" in options:
                trimmed["none"] = options["none"]
            options = trimmed

        compact_state = {k: _clean_opt(str(v), max_len=140) for k, v in state.items() if str(v).strip()}

        if self.decision_model is not None and len(options) >= 2:
            try:
                verdict = await self.decision_model.choice(compact_state, instructions, options)
                if (
                    verdict.order_consistent
                    and not verdict.truncated
                    and verdict.margin >= self.min_margin
                    and verdict.top in options
                ):
                    self.stats.laya_decisions += 1
                    return verdict.top, "laya"
                logger.debug(
                    "Laya incerta em choice (top=%s, margin=%.3f, consistent=%s, truncated=%s); escalando.",
                    verdict.top,
                    verdict.margin,
                    verdict.order_consistent,
                    verdict.truncated,
                )
            except Exception as exc:
                logger.debug("Laya choice indisponível: %s", exc)

        if self._can_escalate_llm():
            llm_key = await self._llm_pick_option(f"{instructions}\nContexto: {json.dumps(compact_state)}", options)
            if llm_key is not None:
                self.stats.llm_escalations += 1
                return llm_key, "llm"

        self.stats.rule_fallbacks += 1
        return fallback_key, "rule"

    async def decide_page_has_form(self, snapshot: SemanticSnapshot) -> tuple[bool, str]:
        """Decide via Laya.noul (com fallback determinístico) se a página atual já exibe o formulário."""
        fields = snapshot.form_inputs
        has_structural_form = (len(fields) >= 2) or (
            len(fields) == 1 and (fields[0].input_type != "search" and snapshot.has_modal)
        )
        if not fields:
            return False, "rule"

        state = {
            "title": _clean_opt(snapshot.title, 70),
            "modal": "yes" if snapshot.has_modal else "no",
            "fields": ", ".join(_clean_opt(f.label or f.input_type or f.tag, 24) for f in fields[:6]),
            "excerpt": _clean_opt(snapshot.body_excerpt, 180),
        }
        instructions = "Responda yes se a tela atual exibe campos de candidatura/formulário de vaga, senão no."
        if self.decision_model is not None:
            try:
                verdict = await self.decision_model.noul(
                    state,
                    instructions,
                    criteria={"yes": "Há campos de formulário da vaga visíveis", "no": "Apenas página informativa"},
                )
                if verdict.band == "yes" and not verdict.truncated:
                    self.stats.laya_decisions += 1
                    await self.emit_visual(
                        title="SYSTEM 1 · LAYA (NOUL)",
                        detail="Formulário visível detectado nesta página.",
                        kind="laya",
                    )
                    return True, "laya"
                if verdict.band == "no" and not verdict.truncated:
                    self.stats.laya_decisions += 1
                    await self.emit_visual(
                        title="SYSTEM 1 · LAYA (NOUL)",
                        detail="Página sem formulário direto — buscando botão de candidatura.",
                        kind="laya",
                    )
                    return False, "laya"
            except Exception as exc:
                logger.debug("Laya noul falhou em decide_page_has_form: %s", exc)

        self.stats.rule_fallbacks += 1
        await self.emit_visual(
            title="REGRAS ESTRUTURAIS",
            detail=(
                "Formulário estrutural identificado."
                if has_structural_form
                else "Sem formulário ativo; procurando ponto de entrada."
            ),
            kind="laya",
        )
        return has_structural_form, "rule"

    async def choose_navigation_action(
        self,
        snapshot: SemanticSnapshot,
        goal: Literal["open_form", "next_step", "submit"] = "open_form",
        visited_labels: set[str] | None = None,
    ) -> tuple[WebElementNode | None, str]:
        """Seleciona o próximo botão/link para abrir o formulário, avançar etapa ou identificar submit."""
        visited = visited_labels or set()
        scored: list[tuple[float, WebElementNode]] = []
        for btn in snapshot.action_buttons:
            sc = _score_action_candidate(btn, goal=goal, visited_labels=visited)
            if sc > 0:
                scored.append((sc, btn))

        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return None, "none"

        top_nodes = [node for _, node in scored[: self.max_options - 1]]
        options = {node.eid: _clean_opt(node.label, 38) for node in top_nodes}
        options["none"] = "nenhuma destas acoes"

        goal_desc = {
            "open_form": "abrir ou iniciar o formulário de candidatura",
            "next_step": "avançar para a próxima etapa sem enviar ainda",
            "submit": "enviar finalmente a candidatura preenchida",
        }[goal]
        state = {
            "goal": goal,
            "title": _clean_opt(snapshot.title, 70),
            "modal": "yes" if snapshot.has_modal else "no",
            "excerpt": _clean_opt(snapshot.body_excerpt, 160),
        }
        chosen_key, decided_by = await self._ask_choice(
            state=state,
            instructions=f"Escolha o elemento clicável exato para {goal_desc}, ou none se nenhum servir.",
            options=options,
            fallback_key=top_nodes[0].eid,
        )
        if chosen_key == "none":
            return None, decided_by
        node_map = {n.eid: n for n in top_nodes}
        chosen = node_map.get(chosen_key)
        if chosen is not None:
            badge = (
                "SYSTEM 2 · LLM" if decided_by == "llm"
                else ("SYSTEM 1 · LAYA" if decided_by == "laya" else "REGRA DETERMINÍSTICA")
            )
            await self.emit_visual(
                title=f"{badge} · NAVEGAÇÃO",
                detail=f'Alvo [{chosen.eid}]: "{chosen.label}" ({goal_desc})',
                kind="llm" if decided_by == "llm" else "laya",
                selector=chosen.selector,
            )
        return chosen, decided_by

    async def map_field_to_profile_key(
        self,
        field: FormQuestion | WebElementNode,
        input_type: str = "",
    ) -> tuple[str, str]:
        """Mapeia um campo desconhecido para uma chave do Profile ou custom_question (com EEO shield)."""
        label = field.label
        if is_sensitive_question(label):
            return "eeo_sensitive", "rule"

        if isinstance(field, WebElementNode) and field.input_type == "file":
            combined = f"{label} {field.name} {field.accept}".lower()
            if _FILE_SKIP_RE.search(combined):
                return "skip_file", "rule"
            return "resume_file", "rule"

        # Textarea representa pergunta aberta dissertativa (System 2), nunca campo escalar de perfil
        is_textarea = input_type == "textarea" or (isinstance(field, FormQuestion) and field.kind == "textarea")
        if is_textarea:
            return "custom_question", "rule"

        # Regra determinística forte primeiro para tipos nativos explícitos
        if input_type in ("email", "tel"):
            self.stats.rule_fallbacks += 1
            return ("email" if input_type == "email" else "phone"), "rule"

        ranked_keys = rank_profile_keys(
            label,
            list(_CANONICAL_PROFILE_KEYS.keys()),
            top_k=max(3, self.max_options - 1),
        )
        rule_hit = _rule_score_field_key(label, input_type)
        if rule_hit and rule_hit not in ranked_keys:
            ranked_keys = [rule_hit, *ranked_keys[: self.max_options - 2]]

        options: dict[str, str] = {
            k: _clean_opt(_CANONICAL_PROFILE_KEYS[k], 34) for k in ranked_keys[: self.max_options - 1]
        }
        options["custom_question"] = "pergunta aberta da vaga"

        fallback_key = rule_hit or "custom_question"
        state = {
            "label": _clean_opt(label, 90),
            "input_type": input_type or getattr(field, "kind", "text"),
        }
        chosen, by = await self._ask_choice(
            state=state,
            instructions="Classifique qual campo do perfil corresponde ao rótulo do formulário, ou custom_question.",
            options=options,
            fallback_key=fallback_key,
        )
        # Se regra forte determinística concorda e caiu em fallback, prioriza a regra
        if by == "rule" and rule_hit:
            return rule_hit, "rule"
        return chosen, by

    async def resolve_profile_fields_with_laya(
        self,
        questions: list[FormQuestion],
        profile: Profile,
        existing: AnswerSet | None = None,
    ) -> dict[str, str]:
        """Mapeia perguntas não-sensíveis via System 1 (Laya) para atributos do Profile antes do System 2."""
        resolved: dict[str, str] = {}
        known = existing.answers if existing is not None else {}
        for q in questions:
            if is_sensitive_question(q.label):
                continue
            cur = known.get(q.id)
            if cur is not None and str(cur).strip():
                continue
            rule_val = resolve_profile_answer(q, profile)
            if rule_val is not None and str(rule_val).strip():
                resolved[q.id] = str(rule_val)
                continue
            mapped_key, _ = await self.map_field_to_profile_key(q, input_type=q.kind)
            if mapped_key in _CANONICAL_PROFILE_KEYS:
                prof_val = _extract_profile_value(profile, mapped_key)
                if prof_val is not None and str(prof_val).strip():
                    resolved[q.id] = str(prof_val)
        return resolved

    async def resolve_select_option(
        self,
        label: str,
        options: list[str],
        desired_value: str | None,
        profile: Profile,
    ) -> str | None:
        """Escolhe opção em <select>/radio usando match determinístico -> Laya.choice -> LLM."""
        if not options or is_sensitive_question(label):
            return None

        if desired_value:
            direct = match_option(desired_value, options)
            if direct:
                return direct

        # Janela compacta <= max_options - 1 para evitar Banking77
        ranked = rank_profile_keys(desired_value or label or profile.location, options, top_k=self.max_options - 1)
        candidates = ranked if ranked else options[: self.max_options - 1]
        opt_map = {f"opt_{i}": _clean_opt(val, 38) for i, val in enumerate(candidates)}
        opt_map["none"] = "nenhuma destas opcoes"

        fallback_key = "opt_0" if (desired_value and candidates) else "none"
        state = {
            "question": _clean_opt(label, 90),
            "fact": _clean_opt(desired_value or f"{profile.work_authorization}; {profile.location}", 110),
        }
        chosen_key, decided_by = await self._ask_choice(
            state=state,
            instructions="Escolha a opção mais compatível com a resposta/fato do candidato, ou none.",
            options=opt_map,
            fallback_key=fallback_key,
        )
        if chosen_key == "none" or not chosen_key.startswith("opt_"):
            return None
        idx = int(chosen_key.split("_", 1)[1])
        chosen_opt = candidates[idx] if 0 <= idx < len(candidates) else None
        if chosen_opt is not None:
            q_label = state.get("question", "Seleção")
            await self.emit_visual(
                title=(
                    "SYSTEM 2 · LLM (ESCALADO)"
                    if decided_by == "llm"
                    else ("SYSTEM 1 · LAYA" if decided_by == "laya" else "MATCH DETERMINÍSTICO")
                ),
                detail=f'Campo "{q_label}" → "{chosen_opt}"',
                kind="llm" if decided_by == "llm" else "laya",
            )
        return chosen_opt

    async def navigate_to_form(
        self,
        page: Page,
        job: Job,
        check_blockers_fn: Callable[[Page], Awaitable[None]],
    ) -> SemanticSnapshot:
        """Explora multi-etapas a partir da URL da vaga até localizar o formulário ativo."""
        visited_labels: set[str] = set()
        snapshot = await extract_semantic_snapshot(page)

        for hop in range(min(self.max_steps, 5)):
            await check_blockers_fn(page)
            snapshot = await extract_semantic_snapshot(page)

            has_form, by = await self.decide_page_has_form(snapshot)
            if has_form:
                self.stats.steps_taken += 1
                self.stats.history.append(
                    NavigationStep(
                        step=self.stats.steps_taken,
                        url=snapshot.url,
                        action="form_found",
                        decided_by=by,
                    )
                )
                return snapshot

            target, by = await self.choose_navigation_action(
                snapshot,
                goal="open_form",
                visited_labels=visited_labels,
            )
            if target is None:
                self.stats.history.append(
                    NavigationStep(step=hop + 1, url=snapshot.url, action="stop", decided_by=by)
                )
                return snapshot

            visited_labels.add(target.label.strip().lower())
            self.stats.steps_taken += 1
            self.stats.history.append(
                NavigationStep(
                    step=self.stats.steps_taken,
                    url=snapshot.url,
                    action="click_cta",
                    target_eid=target.eid,
                    target_label=target.label,
                    decided_by=by,
                )
            )
            await self._safe_click(page, target.selector)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            await page.wait_for_timeout(300)

        return await extract_semantic_snapshot(page)

    def extract_questions_from_snapshot(self, snapshot: SemanticSnapshot) -> list[FormQuestion]:
        """Converte campos visíveis (exceto uploads/botões) em `FormQuestion` compatíveis com Pipeline."""
        questions: list[FormQuestion] = []
        seen_radio_groups: dict[str, dict[str, Any]] = {}

        for node in snapshot.form_inputs:
            if node.input_type == "file":
                continue

            clean_lbl = _clean_label(node.label) or node.name or node.eid
            if node.input_type == "radio":
                grp_key = node.group_name or clean_lbl.lower()
                if grp_key not in seen_radio_groups:
                    seen_radio_groups[grp_key] = {
                        "id": node.group_name or node.eid,
                        "label": clean_lbl,
                        "required": node.required,
                        "options": [],
                    }
                    questions.append(
                        FormQuestion(
                            id=seen_radio_groups[grp_key]["id"],
                            label=clean_lbl,
                            kind="select",
                            required=node.required,
                            options=seen_radio_groups[grp_key]["options"],
                        )
                    )
                for opt in node.options:
                    if opt not in seen_radio_groups[grp_key]["options"]:
                        seen_radio_groups[grp_key]["options"].append(opt)
                continue

            kind: Literal["text", "textarea", "select", "checkbox"] = "text"
            if node.tag == "textarea":
                kind = "textarea"
            elif node.tag == "select":
                kind = "select"
            elif node.input_type == "checkbox":
                kind = "checkbox"

            q_id = node.name or node.eid
            questions.append(
                FormQuestion(
                    id=q_id,
                    label=clean_lbl,
                    kind=kind,
                    required=node.required,
                    options=list(node.options),
                )
            )

        return questions

    async def fill_and_advance_steps(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        resume: ResumeInfo,
        answers: AnswerSet,
        dry_run: bool,
        check_blockers_fn: Callable[[Page], Awaitable[None]],
        eeo_chooser: Callable[[Any, str, dict[str, str]], Awaitable[None]],
        empty_required_fn: Callable[[Page, bool], Awaitable[str | None]],
        settings: Settings | None = None,
    ) -> str | None:
        """Preenche formulários simples ou multi-etapas e retorna o seletor do botão submit final."""
        page = self.wrap_page(page)
        answers_by_label = {_normalize(k): v for k, v in answers.answers.items() if v is not None}
        visited_next: set[str] = set()

        for _ in range(self.max_steps):
            await check_blockers_fn(page)
            snapshot = await extract_semantic_snapshot(page)
            await self._fill_current_step(
                page=page,
                snapshot=snapshot,
                job=job,
                profile=profile,
                resume=resume,
                answers=answers,
                answers_by_label=answers_by_label,
                eeo_chooser=eeo_chooser,
                settings=settings,
            )

            next_btn, next_by = await self.choose_navigation_action(
                snapshot,
                goal="next_step",
                visited_labels=visited_next,
            )
            submit_btn, _ = await self.choose_navigation_action(snapshot, goal="submit")

            # Avança somente se houver botão explícito de próxima etapa distinto do submit
            if next_btn is not None and (submit_btn is None or next_btn.eid != submit_btn.eid):
                # Invariante Zero-Guess: se há campo obrigatório vazio nesta etapa, não avança às cegas
                empty_req = await empty_required_fn(page, dry_run)
                if empty_req is not None:
                    return submit_btn.selector if submit_btn else next_btn.selector

                visited_next.add(next_btn.label.strip().lower())
                self.stats.steps_taken += 1
                self.stats.history.append(
                    NavigationStep(
                        step=self.stats.steps_taken,
                        url=snapshot.url,
                        action="click_next",
                        target_eid=next_btn.eid,
                        target_label=next_btn.label,
                        decided_by=next_by,
                    )
                )
                await self._safe_click(page, next_btn.selector)
                await self.clear_visual_highlight()
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("domcontentloaded", timeout=3000)
                await page.wait_for_timeout(300)
                continue

            if submit_btn is not None:
                return submit_btn.selector
            break

        final_snapshot = await extract_semantic_snapshot(page)
        submit_btn, _ = await self.choose_navigation_action(final_snapshot, goal="submit")
        return submit_btn.selector if submit_btn else None

    async def _fill_current_step(
        self,
        page: Page,
        snapshot: SemanticSnapshot,
        job: Job,
        profile: Profile,
        resume: ResumeInfo,
        answers: AnswerSet,
        answers_by_label: dict[str, str],
        eeo_chooser: Callable[[Any, str, dict[str, str]], Awaitable[None]],
        settings: Settings | None = None,
    ) -> None:
        """Preenche todos os campos visíveis da etapa corrente com proteção EEO e Laya/LLM."""
        step_questions = self.extract_questions_from_snapshot(snapshot)
        q_by_id = {q.id: q for q in step_questions}
        q_by_label = {_normalize(q.label): q for q in step_questions}

        # Resposta dinâmica em lote para novas perguntas não-sensíveis reveladas em etapas posteriores
        unanswered_non_sensitive: list[FormQuestion] = []
        for q in step_questions:
            if is_sensitive_question(q.label):
                continue
            if answers.answers.get(q.id) is not None or answers_by_label.get(_normalize(q.label)) is not None:
                continue
            if q.id in self._dynamic_answer_cache:
                continue
            if q.kind != "textarea" and resolve_profile_answer(q, profile) is not None:
                continue
            if q.kind == "textarea" and not self._can_escalate_llm() and resolve_profile_answer(q, profile) is not None:
                continue
            if q.kind in ("text", "textarea", "select"):
                unanswered_non_sensitive.append(q)

        if unanswered_non_sensitive and self._can_escalate_llm() and self.matcher is not None:
            with contextlib.suppress(Exception):
                dyn_set = await self.matcher.answer_questions(job, unanswered_non_sensitive, profile, resume.text)
                for q_id, val in dyn_set.answers.items():
                    self._dynamic_answer_cache[q_id] = val
                    if val is not None:
                        self.stats.llm_escalations += 1
                        answers.answers[q_id] = val

        handled_radio_groups: set[str] = set()
        for node in snapshot.form_inputs:
            if node.input_type == "radio":
                grp_key = node.group_name or _clean_label(node.label).lower()
                if grp_key in handled_radio_groups:
                    continue
                handled_radio_groups.add(grp_key)
                await self._fill_radio_group(page, snapshot, grp_key, profile, answers, answers_by_label)
                continue

            if node.input_type == "file":
                p_key, _ = await self.map_field_to_profile_key(node, input_type="file")
                if p_key == "resume_file" and resume.path.exists():
                    with contextlib.suppress(Exception):
                        await page.locator(node.selector).set_input_files(str(resume.path))
                continue

            clean_lbl = _clean_label(node.label) or node.name or node.eid
            q_obj = q_by_id.get(node.name or node.eid) or q_by_label.get(_normalize(clean_lbl))
            if q_obj is None:
                q_obj = FormQuestion(id=node.name or node.eid, label=clean_lbl, required=node.required)

            # Invariante EEO/LGPD: pergunta sensível NUNCA passa por Laya ou LLM
            if is_sensitive_question(clean_lbl):
                await self._fill_sensitive_field(page, node, clean_lbl, profile, answers, eeo_chooser)
                continue

            val = await self._resolve_field_value(node, q_obj, profile, answers, answers_by_label)
            if node.input_type == "checkbox":
                should_check = _should_check_checkbox(clean_lbl, node.required, val)
                if should_check and not node.checked:
                    with contextlib.suppress(Exception):
                        await page.locator(node.selector).check()
                continue

            if node.tag == "select":
                chosen_opt = await self.resolve_select_option(clean_lbl, node.options, val, profile)
                if chosen_opt is not None:
                    with contextlib.suppress(Exception):
                        await page.locator(node.selector).select_option(label=chosen_opt)
                continue

            if (
                (val is None or not str(val).strip())
                and settings is not None
                and getattr(settings, "AUTONOMOUS_MODE", False)
                and node.required
                and node.input_type == "number"
            ):
                val = "2"
            if val is None or not str(val).strip():
                continue

            if not node.value.strip():
                with contextlib.suppress(Exception):
                    await page.locator(node.selector).fill(str(val).strip())

    async def _fill_sensitive_field(
        self,
        page: Page,
        node: WebElementNode,
        label: str,
        profile: Profile,
        answers: AnswerSet,
        eeo_chooser: Callable[[Any, str, dict[str, str]], Awaitable[None]],
    ) -> None:
        """Preenche campo EEO/sensível deterministicamente sem nunca enviar para Laya ou LLM."""
        self.eeo_hits += 1
        await self.emit_visual(
            title="ESCUDO EEO / PRIVACIDADE",
            detail=f'Campo sensível "{label}" preenchido localmente (nunca enviado para Laya/LLM).',
            kind="eeo",
            selector=node.selector,
        )
        loc = page.locator(node.selector)
        if node.tag == "select":
            await eeo_chooser(loc, label, profile.eeo)
            return

        ans = answers.answers.get(node.name or node.eid)
        if not ans:
            norm = _normalize(label)
            for ek, ev in profile.eeo.items():
                if _normalize(ek) in norm:
                    ans = ev
                    break
        if ans and node.tag in ("input", "textarea") and not node.value.strip():
            with contextlib.suppress(Exception):
                await loc.fill(ans)

    async def _fill_radio_group(
        self,
        page: Page,
        snapshot: SemanticSnapshot,
        grp_key: str,
        profile: Profile,
        answers: AnswerSet,
        answers_by_label: dict[str, str],
    ) -> None:
        """Seleciona uma opção em grupo de botões radio respeitando EEO shield e Laya."""
        group_nodes = [
            n
            for n in snapshot.form_inputs
            if n.input_type == "radio" and (n.group_name or _clean_label(n.label).lower()) == grp_key
        ]
        if not group_nodes or any(n.checked for n in group_nodes):
            return

        group_label = _clean_label(group_nodes[0].label) or grp_key
        options = [n.options[0] if n.options else n.value for n in group_nodes]
        options = [o for o in options if o]
        if not options:
            return

        desired: str | None = None
        if is_sensitive_question(group_label):
            norm_lbl = _normalize(group_label)
            for ek, ev in profile.eeo.items():
                if _normalize(ek) in norm_lbl:
                    desired = match_option(ev, options)
                    break
            if not desired:
                for opt in options:
                    if any(p in opt.lower() for p in ("prefer not", "decline", "não declarar", "prefiro não")):
                        desired = opt
                        break
            if not desired:
                desired = options[0]
        else:
            desired = answers.answers.get(grp_key) or answers_by_label.get(_normalize(group_label))
            if not desired:
                q_obj = FormQuestion(id=grp_key, label=group_label, kind="select", options=options)
                desired = resolve_profile_answer(q_obj, profile)
            chosen = await self.resolve_select_option(group_label, options, desired, profile)
            desired = chosen or (match_option(desired, options) if desired else None)

        if not desired:
            return

        for node in group_nodes:
            opt_txt = node.options[0] if node.options else node.value
            if opt_txt.strip().lower() == desired.strip().lower():
                with contextlib.suppress(Exception):
                    await page.locator(node.selector).check()
                return

    async def _resolve_field_value(
        self,
        node: WebElementNode,
        q_obj: FormQuestion,
        profile: Profile,
        answers: AnswerSet,
        answers_by_label: dict[str, str],
    ) -> str | None:
        """Resolve valor de campo combinando AnswerSet, regras, base_answers e mapeamento Laya."""
        direct = answers.answers.get(q_obj.id) or answers_by_label.get(_normalize(q_obj.label))
        if direct is not None and str(direct).strip():
            return str(direct)

        dyn_val = self._dynamic_answer_cache.get(q_obj.id)
        if dyn_val is not None and str(dyn_val).strip():
            return str(dyn_val)

        if node.tag == "textarea" and self._can_escalate_llm():
            return None

        rule_val = resolve_profile_answer(q_obj, profile)
        if rule_val is not None and str(rule_val).strip():
            return str(rule_val)

        norm_label = re.sub(r"[^\w\s]", " ", _normalize(q_obj.label))
        label_tokens = set(norm_label.split())
        for bk, bv in profile.base_answers.items():
            clean_bk = re.sub(r"[^\w\s]", " ", _normalize(bk).replace("_", " "))
            key_tokens = clean_bk.split()
            if key_tokens and all(tok in label_tokens for tok in key_tokens) and bv.strip():
                return bv

        mapped_key, _ = await self.map_field_to_profile_key(node, input_type=node.input_type)
        if mapped_key in _CANONICAL_PROFILE_KEYS:
            return _extract_profile_value(profile, mapped_key)

        return self._dynamic_answer_cache.get(q_obj.id)

    async def _safe_click(self, page: Page, selector: str) -> None:
        """Clica com fallback seguro para overlays ou elementos fora da viewport."""
        loc = page.locator(selector).first
        with contextlib.suppress(Exception):
            await loc.scroll_into_view_if_needed(timeout=2000)
        try:
            await loc.click(timeout=5000)
        except Exception:
            try:
                await loc.click(force=True, timeout=5000)
            except Exception:
                await loc.evaluate("el => el.click()")


def _should_check_checkbox(label: str, required: bool, answer_val: str | None) -> bool:
    """Determina se um checkbox deve ser marcado com segurança."""
    if answer_val is not None:
        return answer_val.strip().lower() in ("yes", "sim", "true", "1", "checked", "aceito", "agree")
    return required or bool(_CONSENT_RE.search(label))


class LayaGenericHandler(BaseATSHandler):
    """Handler universal de `BaseATSHandler` guiado pelo `LayaWebNavigator`."""

    name = "laya_generic"

    def __init__(
        self,
        decision_model: DecisionModel | None = None,
        matcher: Matcher | None = None,
        navigator: LayaWebNavigator | None = None,
        settings: Settings | None = None,
        min_margin: float = 0.12,
        max_options: int = 8,
        max_steps: int = 8,
        visual_watch: bool = False,
        visual_delay_ms: int = 750,
        engine_label: str = "",
        on_visual_event: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.navigator = navigator or LayaWebNavigator(
            decision_model=decision_model,
            matcher=matcher,
            min_margin=min_margin,
            max_options=max_options,
            max_steps=max_steps,
            visual_watch=visual_watch,
            visual_delay_ms=visual_delay_ms,
            engine_label=engine_label,
            on_visual_event=on_visual_event,
        )

    def wrap_visual_page(self, page: Page, *, intercept_all: bool = False) -> Page:
        """Envolve `page` com o proxy visual/HUD da Laya quando habilitado."""
        return self.navigator.wrap_page(page, intercept_all=intercept_all)

    def can_handle(self, job: Job) -> bool:
        """Aceita qualquer URL HTTP/HTTPS válida quando ativado como fallback genérico."""
        return self.validate_job_url(job.url)

    async def _check_blockers(self, page: Page) -> None:
        """Verifica widgets padrão de CAPTCHA/login e desafios textuais em portais desconhecidos."""
        await super()._check_blockers(page)
        with contextlib.suppress(Exception):
            body_text = (await page.inner_text("body")).strip()[:2000]
            if body_text and has_blocker_marker(body_text):
                has_inputs = await page.locator("input:not([type='hidden']):not([type='search']), textarea").count() > 0
                if not has_inputs:
                    raise BlockerDetected("Bloqueio antibot/CAPTCHA textual detectado na página.")

    async def inspect(self, page: Page, job: Job) -> PageInfo:
        """Navega até o formulário via Laya, verifica blockers e extrai perguntas."""
        if not self.validate_job_url(job.url):
            raise ValueError(f"Job {job.id} possui URL inválida ou ausente: {job.url!r}")

        self.navigator.reset_stats()
        await page.goto(str(job.url), wait_until="domcontentloaded")
        await self._check_blockers(page)

        initial_description = ""
        with contextlib.suppress(Exception):
            initial_description = (await page.inner_text("body")).strip()[:3000]

        snapshot = await self.navigator.navigate_to_form(page, job, self._check_blockers)
        questions = self.navigator.extract_questions_from_snapshot(snapshot)

        description = initial_description or snapshot.body_excerpt
        signals = await self._collect_page_signals(page, description)
        summary = (
            f"LayaGeneric '{job.title}' em '{job.company}': {len(questions)} campos descobertos "
            f"em {self.navigator.stats.steps_taken} passos."
        )
        return PageInfo(
            description=description,
            questions=questions,
            state_summary=summary,
            page_signals=signals,
        )

    async def fill_and_submit(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        resume: ResumeInfo,
        answers: AnswerSet,
        dry_run: bool = True,
        ready_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> ApplicationResult:
        """Preenche todas as etapas descobertas e delega validação/submit para `_finalize`."""
        if (not page.url or page.url == "about:blank") and self.validate_job_url(job.url):
            await page.goto(str(job.url), wait_until="domcontentloaded")
            await self.navigator.navigate_to_form(page, job, self._check_blockers)

        await self._check_blockers(page)

        submit_selector = await self.navigator.fill_and_advance_steps(
            page=page,
            job=job,
            profile=profile,
            resume=resume,
            answers=answers,
            dry_run=dry_run,
            check_blockers_fn=self._check_blockers,
            eeo_chooser=self._choose_eeo_option,
            empty_required_fn=self._find_empty_required,
            settings=self.settings,
        )
        if not submit_selector:
            submit_selector = "button[type='submit'], input[type='submit']"

        return await self._finalize(
            page=page,
            job=job,
            resume=resume,
            dry_run=dry_run,
            ready_check=ready_check,
            submit_selector=submit_selector,
            confirmation_selector=_GENERIC_CONFIRM_SELECTOR,
        )
