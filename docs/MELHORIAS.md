# MELHORIAS.md — Plano de melhorias do `ia_candidata`

> **Leitor-alvo: um agente de IA que vai EXECUTAR estas mudanças.**
> Leia a seção 0 inteira antes de tocar em qualquer arquivo.

---

## 0. Regras de execução (obrigatórias)

1. **Ordem**: execute as tarefas na ordem dos IDs (`SEC-*` → `BUG-*` → `SAFE-*` → `REF-*` → `QA-*`). Elas são independentes salvo quando indicado em **Depende de**.
2. **Uma tarefa por vez**: implemente → rode `.\.venv\Scripts\python.exe -m pytest -q` → só avance com a suíte verde.
3. **Não quebre invariantes de segurança** (estão em `docs/PLAN.md` e no docstring de `core/decision/layer.py`):
   - `DRY_RUN=True` é o padrão; nada é enviado sem opt-in explícito.
   - Perguntas sensíveis (EEO) **nunca** vão ao LLM nem ao Laya.
   - Laya **nunca** remove um bloqueio imposto por regra determinística (DP3/DP4/DP6).
   - Erro após o clique em submit **nunca** é re-tentado.
   - Ausência de fato no perfil **nunca** é preenchida com valor inventado → `NEEDS_REVIEW`.
4. **Testes**: toda correção de bug ganha um teste unitário em `tests/unit/` que falha antes e passa depois. Use os fixtures de `tests/conftest.py`. Sem rede real, sem browser real nos testes unitários.
5. **Estilo**: Python 3.11+, `from __future__ import annotations`, type hints, docstrings e mensagens de log em **português**, nomes de código em inglês. Preserve comentários existentes que não tenham relação com a mudança.
6. **Não altere** a assinatura pública de `BaseATSHandler`, `DecisionLayer.decide_*` e `Pipeline.process_job` sem atualizar todos os chamadores e testes.
7. Ambiente: Windows + PowerShell. Use `.\.venv\Scripts\python.exe`. Não use comandos `cd`.
8. Ao terminar cada tarefa, marque `[x]` no checklist da seção 7 deste arquivo.
9. **Linha de base** (antes de qualquer mudança, 04/10/2026): `99 passed, 1 deselected` (~40 s). A contagem só pode subir.
10. **FORA DE ESCOPO — não faça** (o dono do projeto vai tratar manualmente):
    - **Não** execute `git init`, commits ou qualquer comando git.
    - **Não** altere `.env` nem a linha `LLM_API_KEY` de `.env.example`. Se precisar adicionar/remover outras variáveis no `.env.example` (ex.: REF-7, BUG-11), edite só as linhas correspondentes.
    - Como não há controle de versão, **antes de editar um arquivo, copie-o** para `artifacts/backup/<caminho_relativo>` (crie as pastas) para permitir reversão manual.

---

## 1. Visão geral do diagnóstico

| Prioridade | Qtde | Tema |
|---|---|---|
| 🔴 Crítico (segurança/dados) | 2 | PII fora do `.gitignore`, OAuth interativo |
| 🟠 Bugs funcionais | 12 | `NameError`, avaliação nunca lida, currículo errado, falso bloqueio, resultados perdidos |
| 🟡 Violação de invariantes | 6 | respostas inventadas, `PostSubmitError` falso, submit sem confirmação |
| 🔵 Refatoração/qualidade | 10 | duplicação nos handlers, recursos não fechados, settings não usadas |
| ⚪ Testes/infra | 5 | requirements, scripts soltos, lint/type-check |

---

## 2. 🔴 Segurança e higiene do repositório

> SEC-1 (git) e SEC-2 (chave no `.env.example`) foram retirados do escopo — ver regra 10 da seção 0.

### SEC-3 — `.gitignore` incompleto
**Arquivo**: [.gitignore](file:///c:/codes/ia_candidata/.gitignore)
**Problema**: lista `oauth_client.json`, mas o arquivo real é `client_secret.json`. `data/profile.json` (PII: nome, e-mail, telefone, EEO) e `data/vagas.csv` não estão ignorados.
**Como fazer**: acrescente:
```gitignore
client_secret.json
*.tmp
data/profile.json
data/vagas.csv
data/vagas.xlsx
.ruff_cache/
```
E crie `data/profile.example.json` com os mesmos campos de `core.models.Profile` e valores fictícios (ex.: `"full_name": "Fulana de Tal"`). Atualize o help de `--profile` em `main.py` mencionando o exemplo.

### SEC-4 — Fluxo OAuth interativo disparado sem necessidade
**Arquivo**: [main.py](file:///c:/codes/ia_candidata/main.py#L160-L167)
**Problema**: quando a fonte é Google Sheets, `DriveClient(settings)` é construído **antes** de tentar o download público. Se não houver `token.json` válido, `get_credentials` chama `flow.run_local_server()` (abre navegador e bloqueia) — inviável em execução agendada. O erro é engolido com `except Exception: pass`.
**Como fazer**:
- Remova a construção antecipada de `DriveClient` em `run_harness`. Passe `drive_service=None` para `download_google_sheet`; ele já faz o fallback autenticado sozinho.
- Em `storage/sheet_loader.py::download_google_sheet`, aceite parâmetros `service_account_file: Path | None` e `oauth_client_file: Path | None` (em vez de caminhos hardcoded `./service_account.json` e `./client_secret.json`), e passe-os a partir de `settings` no `main.py`.
- Em `storage/drive_client.py::get_credentials`, adicione parâmetro `interactive: bool = True`. Quando `False` e não houver token válido, lance `DriveAccessError("token OAuth ausente/expirado; rode scripts/auth_google.py")` em vez de abrir o navegador. Use `interactive=False` em todos os caminhos chamados por `main.py`; mantenha `True` apenas em `scripts/auth_google.py`.
- Torne o caminho do `token.json` configurável: nova setting `GOOGLE_TOKEN_FILE: Path = Path("./token.json")`.

---

## 3. 🟠 Bugs funcionais

### BUG-1 — `NameError: log` em `ResumeResolver.load_cache`
**Arquivo**: [core/resume_resolver.py](file:///c:/codes/ia_candidata/core/resume_resolver.py#L170)
**Problema**: `log.warning(...)` é chamado, mas o módulo não importa `logging` nem define `log`. Qualquer PDF ilegível derruba a indexação com `NameError`.
**Como fazer**: adicione `import logging` e `log = logging.getLogger(__name__)` no topo.
**Teste**: crie um arquivo `.pdf` corrompido em `tmp_path`, chame `load_cache()` e verifique que não lança exceção e que o arquivo não está em `get_resumes()`.

### BUG-2 — `.docx`/`.doc` indexados como PDF
**Arquivo**: [core/resume_resolver.py](file:///c:/codes/ia_candidata/core/resume_resolver.py#L18), linhas 159-167
**Problema**: `_EXTENSIONS` inclui `.docx`/`.doc`, mas `load_cache` sempre usa `extract_pdf_text` → sempre falha para Word.
**Como fazer**: em `load_cache`, indexe apenas `.pdf` (mantenha `_EXTENSIONS` para `normalize`/`_strip_extension`). Crie constante `_INDEXABLE = (".pdf",)`. Logue em `info` os arquivos ignorados por extensão.

### BUG-3 — Relatório de avaliação do Laya nunca é lido
**Arquivos**: [scripts/eval_decisions.py](file:///c:/codes/ia_candidata/scripts/eval_decisions.py#L161), [config/settings.py](file:///c:/codes/ia_candidata/config/settings.py#L97)
**Problema**: o script grava por padrão em `artifacts/decision_eval.json`, mas `main.py`/`DecisionLayer` leem `settings.LAYA_EVAL_FILE` = `artifacts/laya_eval.json`. Resultado: DP1/DP2/DP5 nunca saem de `shadow`, mesmo aprovados.
**Como fazer**: em `main()` do script, use `default=None` em `--out`; após carregar `settings`, `out = args.out or settings.LAYA_EVAL_FILE`. Passe `out` para `evaluate`.

### BUG-4 — Calibração/avaliação estatisticamente incorreta
**Arquivo**: [scripts/eval_decisions.py](file:///c:/codes/ia_candidata/scripts/eval_decisions.py#L88-L105)
**Problemas**:
- noul: `uncertain` é contado como predição `"no"`; deveria ser abstenção (cobertura), não acerto/erro de `no`.
- ECE recebe `p_true` como confiança, mas a confiança da predição `no` é `1 - p_true`.
- choice: `band = "yes" if order_consistent` ignora `LAYA_MIN_MARGIN`, que é o critério real usado em produção (`layer.py` L283-287, L486-491).
**Como fazer**:
- noul: `pred = {"yes": "yes", "no": "no", "uncertain": "abstain"}[band]`; confiança `= p_true if pred == "yes" else 1 - p_true`.
- choice: `qualifies = verdict.order_consistent and verdict.margin >= settings.LAYA_MIN_MARGIN`; `band = "yes" if qualifies else "uncertain"`; se não qualifica, `pred = "abstain"`.
- Calcule `accuracy` **apenas sobre as não-abstenções** (`selective_accuracy`) e mantenha `coverage`. Adicione a `core/decision/evaluation.py` a função pura `selective_accuracy(predictions, labels, abstain="abstain")`.
- Em `passes()`, exija também `coverage >= 0.5` (nova setting `EVAL_MIN_COVERAGE: float = 0.5`).
- Atualize `tests/unit/test_decision_evaluation.py`.

### BUG-5 — Fallback de currículo pode usar o currículo otimizado de OUTRA vaga
**Arquivo**: [core/pipeline.py](file:///c:/codes/ia_candidata/core/pipeline.py#L95-L107)
**Problema**: `base_resumes = self.resume_resolver.get_resumes()` devolve **todos** os arquivos, inclusive `Currículo Otimizado <Outra Empresa>.pdf`. O DP2/LLM pode escolher um desses e enviá-lo.
**Como fazer**:
- Adicione em `ResumeResolver` o método `get_base_resumes(self, base_names: list[str] | None = None) -> dict[str, ResumeInfo]`:
  - Se `base_names` (vem de `job.base_used`) não for vazio, retorne `resolve_bases(base_names, self._resumes, self.threshold)` re-chaveado pelo **nome do arquivo** (`info.path.name`).
  - Se vazio ou resultado vazio, retorne `{n: i for n, i in self._resumes.items() if not is_optimized_name(n)}`.
- No pipeline, use `self.resume_resolver.get_base_resumes(job.base_used)`.
**Teste**: resolver com `Currículo Otimizado Acme.pdf` + `Base Inglês.pdf`; job de outra empresa sem rótulo → o dicionário passado ao fallback não contém o arquivo "Otimizado".

### BUG-6 — Descrição da vaga extraída da página é descartada
**Arquivo**: [core/pipeline.py](file:///c:/codes/ia_candidata/core/pipeline.py#L131-L201)
**Problema**: `page_info.description` nunca é usada. A planilha não tem coluna de descrição, então `job.description` é quase sempre `None` e `Matcher.answer_questions` envia `str(job)` (repr do Pydantic) ao LLM.
**Como fazer**: logo após `page_info = await handler.inspect(...)`:
```python
if not job.description and page_info.description:
    job = job.model_copy(update={"description": page_info.description})
```
Em `core/matcher.py::answer_questions`, troque `or str(job)` por `or f"{job.title} — {job.company}"` e tipar `job: Job` (importe de `core.models`).
> Observação: DP1 roda antes da inspeção, então continua sem descrição — está ok; não reordene.

### BUG-7 — DP3 dá falso positivo e nunca vê sinais reais da página
**Arquivos**: [core/decision/layer.py](file:///c:/codes/ia_candidata/core/decision/layer.py#L320), handlers (`state_summary`)
**Problema**: `has_blocker_marker(page_info.state_summary)` analisa um texto sintético como `"Lever job 'X' em 'Cloudflare' com 3 perguntas"`. Uma vaga **na empresa Cloudflare** (ou título com "bot detection") é sempre bloqueada; e um CAPTCHA real nunca aparece nesse texto.
**Como fazer**:
- Adicione a `PageInfo` (em `core/models.py`) o campo `page_signals: str = ""` — texto **sem PII e sem nome da empresa/cargo**: título da página, host da URL, e os primeiros ~500 caracteres de `body.inner_text()` que não pertençam à descrição da vaga.
- Crie em `BaseATSHandler` (ver REF-1) um helper `async def _collect_page_signals(self, page) -> str` e chame-o ao fim de cada `inspect`.
- Em `decide_blocker`, use `has_blocker_marker(page_info.page_signals)` e passe `page_signals` para `build_dp3_blocker_state` (mantenha `state_summary` apenas como informação secundária).
**Teste**: `PageInfo(state_summary="Lever job 'SRE' em 'Cloudflare'", page_signals="")` → `decide_blocker` retorna `False` em modo `off`.

### BUG-8 — Resultados de vagas com status em branco são descartados na planilha
**Arquivo**: [storage/sheet_writer.py](file:///c:/codes/ia_candidata/storage/sheet_writer.py#L63)
**Problema**: `write_results` recarrega as vagas com `load_jobs(source)` (padrão `include_blank=False`). Com `--include-blank-status`, as vagas de status vazio não estão no mapa → `"resultado sem linha correspondente"` e o resultado se perde. Além disso, os IDs com sufixo `-2` dependem do filtro aplicado.
**Como fazer**:
- Mude a assinatura para `write_results(source_path, results, out_path, jobs: dict[str, Job], now=None)` e use o `jobs` recebido (o mesmo usado na execução). Remova a chamada interna a `load_jobs`.
- Em `main.py`, construa `jobs_map = {j.id: j for j in jobs}` **uma vez** e reutilize para `write_results` e `update_google_sheet_results`.
- Atualize `tests/unit/test_sheet_writer.py`.

### BUG-9 — `LayaSidecarClient.choice` lança exceções não tratadas
**Arquivo**: [core/decision/laya_client.py](file:///c:/codes/ia_candidata/core/decision/laya_client.py#L279-L307)
**Problema**: `data1["answers"]` (KeyError), `next(iter(...))` em dict vazio (StopIteration), `sorted_p[0]` com lista vazia (IndexError), `float(v)` (ValueError). O `DecisionLayer` só captura `DecisionUnavailable` → a vaga inteira vira `FAILED`.
**Como fazer**: extraia `_parse_choice_answer(data: dict, options: dict) -> tuple[dict[str, float], str]` que envolve o parsing num `try/except (KeyError, TypeError, ValueError, StopIteration) as exc: raise DecisionUnavailable(...) from exc`, e valide que `probs` não é vazio. Use nas duas chamadas.
**Teste**: com `respx`, responda `{"ok": true, "answers": {}}` → `choice()` lança `DecisionUnavailable`.

### BUG-10 — `LayaSidecarClient` quebra com `settings=None`
**Arquivo**: [core/decision/laya_client.py](file:///c:/codes/ia_candidata/core/decision/laya_client.py#L42-L60)
**Problema**: o construtor aceita `settings=None`, mas `_record_failure`, `health` (modo degradado) e `choice` (`LAYA_ORDER_SWAP`) acessam `self._settings.X` sem checagem → `AttributeError`.
**Como fazer**: no `__init__`, guarde valores resolvidos em atributos (`self._max_failures`, `self._order_swap`, `self._yes_th`, `self._no_th`, `self._cpu_factor`, `self._base_timeout`, `self._plan_above`) com defaults quando `settings is None`, e use apenas esses atributos no resto da classe. Unifique o default de URL com `Settings.LAYA_URL` (`http://127.0.0.1:8787`, hoje está `8123`).

### BUG-11 — Circuit breaker nunca fecha
**Arquivo**: [core/decision/laya_client.py](file:///c:/codes/ia_candidata/core/decision/laya_client.py#L129-L168)
**Problema**: após `LAYA_MAX_FAILURES`, o breaker fica aberto até o fim do processo.
**Como fazer**: implemente half-open: guarde `self._opened_at = time.monotonic()`; em `_post_ask`, se aberto e `monotonic() - opened_at >= LAYA_BREAKER_COOLDOWN_S` (nova setting, default `60.0`), permita **uma** requisição de teste; sucesso → `reset_breaker()`, falha → reabre e renova `opened_at`.

### BUG-12 — `Matcher._post` ignora settings e o `sleep` injetado
**Arquivo**: [core/matcher.py](file:///c:/codes/ia_candidata/core/matcher.py#L93-L109)
**Problema**: loop fixo de 3 tentativas com `asyncio.sleep` direto (não usa `self._sleep` → testes lentos; não usa `LLM_MAX_RETRIES`/`BACKOFF_BASE_S`). Combinado com `_ask_json`, pode chegar a 9 chamadas por pergunta. Não respeita `Retry-After` em 429.
**Como fazer**: use `attempts = max(1, self._settings.LLM_MAX_RETRIES)`, `await self._sleep(self._settings.BACKOFF_BASE_S * 2**attempt)` e, se houver header `Retry-After` numérico, use `min(float(retry_after), 30.0)`. Remova o `assert response is not None` substituindo por fluxo explícito.

---

## 4. 🟡 Violações de invariantes de segurança

### SAFE-1 — Handler Lever inventa resposta em `<select>`
**Arquivo**: [automation/handlers/lever.py](file:///c:/codes/ia_candidata/automation/handlers/lever.py#L357-L361)
**Problema**: se a resposta não casa com nenhuma opção, seleciona **a primeira opção válida**. Isso viola "nunca inventar dados".
**Como fazer**: remova o bloco `if not selected: ...`. Se não selecionou e a pergunta é obrigatória, deixe vazio — a verificação de obrigatórios (passo 6) devolverá `NEEDS_REVIEW`. Logue em `warning` sem o valor da resposta.

### SAFE-2 — Valores fabricados em campos de perfil
**Arquivos**: [lever.py L239-241](file:///c:/codes/ia_candidata/automation/handlers/lever.py#L239-L241), [greenhouse.py L199-201](file:///c:/codes/ia_candidata/automation/handlers/greenhouse.py#L199-L201), [lever.py L278-279](file:///c:/codes/ia_candidata/automation/handlers/lever.py#L278-L279)
**Problema**: `org = "Self-Employed / Engineer"` quando não há headline; `last_name = "-"` quando o nome tem uma palavra; o campo "comments" recebe a headline sem que isso tenha sido pedido.
**Como fazer**:
- Adicione a `Profile` o campo opcional `current_company: str | None = None`. Preencha `ORG` só com `current_company`; se ausente, não preencha.
- Greenhouse: se não houver sobrenome, não preencha `LAST_NAME` (a validação de obrigatórios cuida do resto).
- Remova o preenchimento automático de `COMMENTS` com headline.

### SAFE-3 — `PostSubmitError` lançado ANTES do submit
**Arquivos**: [lever.py L419-420](file:///c:/codes/ia_candidata/automation/handlers/lever.py#L419-L420), [greenhouse.py L353-354](file:///c:/codes/ia_candidata/automation/handlers/greenhouse.py#L353-L354), [aggregator.py L477-478 e L512-513](file:///c:/codes/ia_candidata/automation/handlers/aggregator.py#L477-L478)
**Problema**: "botão de submit não encontrado" e "campo de upload não encontrado" lançam `PostSubmitError`, que marca `submitted=True` no resultado e na planilha — mas nada foi enviado.
**Como fazer**: nesses casos, retorne `ApplicationResult(status=NEEDS_REVIEW, submitted=False, reason=...)`. `PostSubmitError` só pode ser lançado **depois** de `submit_btn.click()`.

### SAFE-4 — Agregador reporta sucesso sem confirmação
**Arquivo**: [aggregator.py L516-531](file:///c:/codes/ia_candidata/automation/handlers/aggregator.py#L516-L531)
**Problema**: após o clique, se a confirmação não é detectada, só loga warning e devolve `COMPLETED / submitted=True`.
**Como fazer**: alinhe com Lever/Greenhouse: `await page.wait_for_selector(CONFIRMATION, timeout=10000)`; em falha, `raise PostSubmitError(...)` (o pipeline converte em `NEEDS_REVIEW` sem retry).

### SAFE-5 — Agregador não verifica obrigatórios nem trata EEO
**Arquivo**: [aggregator.py L424-497](file:///c:/codes/ia_candidata/automation/handlers/aggregator.py#L424-L497)
**Problema**: diferentemente de Lever/Greenhouse, não há verificação de `[required]` vazio antes do `ready_check`, e `is_sensitive_question(q_id)` testa o **id**, não o rótulo.
**Como fazer**: após REF-1, chame `await self._find_empty_required(page)` e `await self._fill_eeo_selects(...)` da classe base. Para filtrar sensíveis, use o `label` inferido em `inspect` (guarde um mapa `id → label` no próprio `PageInfo.questions`).

### SAFE-6 — Verificação de obrigatórios ignora checkbox/radio
**Arquivos**: [lever.py L378-391](file:///c:/codes/ia_candidata/automation/handlers/lever.py#L378-L391), [greenhouse.py L312-325](file:///c:/codes/ia_candidata/automation/handlers/greenhouse.py#L312-L325)
**Problema**: `input_value()` em checkbox/radio retorna o atributo `value` (`"on"`), não o estado marcado → obrigatório desmarcado passa como preenchido. Inputs `type=file` sempre contam como `"uploaded"`, mesmo sem arquivo.
**Como fazer** (no helper de REF-1):
- `checkbox`: `await el.is_checked()`.
- `radio`: verifique se algum `input[type=radio][name="<name>"]` está marcado.
- `file`: `await el.evaluate("e => e.files && e.files.length > 0")`.
- ignore elementos `hidden`/`disabled` (`await el.is_visible()` e `await el.is_enabled()`).

---

## 5. 🔵 Refatoração e qualidade

### REF-1 — Extrair código duplicado dos handlers para `BaseATSHandler`
**Depende de**: nada (mas facilita SAFE-5, SAFE-6, BUG-7).
**Arquivos**: [base_handler.py](file:///c:/codes/ia_candidata/automation/base_handler.py), `lever.py`, `greenhouse.py`, `aggregator.py`
**Problema**: `_check_blockers`, seleção de EEO, verificação de obrigatórios, bloco `ready_check → dry_run → submit → confirmação` e o casamento de respostas por rótulo estão copiados 2-3 vezes, com pequenas divergências (fonte de bugs como SAFE-1/3/4).
**Como fazer** — adicione métodos **concretos** (não abstratos) em `BaseATSHandler`:
```python
CAPTCHA_SELECTORS: tuple[str, ...] = (...)   # união dos 3 handlers
LOGIN_SELECTORS: tuple[str, ...] = (...)
DECLINE_TERMS: tuple[str, ...] = ("decline", "don't wish", "not to say", "não informar", "prefiro não")

async def _check_blockers(self, page: Page) -> None: ...
async def _collect_page_signals(self, page: Page) -> str: ...             # BUG-7
async def _choose_eeo_option(self, select: Locator, label: str, eeo: dict[str, str]) -> None: ...
async def _find_empty_required(self, page: Page) -> str | None: ...       # SAFE-6; retorna nome do 1º vazio
async def _finalize(self, page, job, resume, dry_run, ready_check,
                    submit_selector: str, confirmation_selector: str) -> ApplicationResult: ...
```
`_finalize` concentra: obrigatórios vazios → `NEEDS_REVIEW`; `ready_check` falso → `NEEDS_REVIEW`; `dry_run` → `COMPLETED/submitted=False`; botão ausente → `NEEDS_REVIEW` (SAFE-3); clique; espera confirmação (`TIMEOUT` = `settings.ACTION_TIMEOUT_MS`) → `COMPLETED/submitted=True` ou `PostSubmitError`.
Cada handler passa a chamar `return await self._finalize(...)`. Mantenha `_check_blockers` sobrescrevível (o agregador estende com URL/título).

### REF-2 — Remover hacks de mock no `AggregatorHandler`
**Arquivo**: [aggregator.py](file:///c:/codes/ia_candidata/automation/handlers/aggregator.py#L137-L166)
**Problema**: `asyncio.iscoroutine(loc)`, `callable(raw_url)`, `import asyncio` dentro de métodos — código de produção adaptado a mocks mal construídos. `page.url` é propriedade síncrona e `page.locator()` é síncrono no Playwright.
**Como fazer**: use `page.url` e `page.locator(...)` diretamente. Corrija `tests/unit/test_aggregator_handler.py` para usar `MagicMock` para objetos síncronos e `AsyncMock` só para métodos `async` (`count`, `is_visible`, `title`, `get_attribute`, ...).
Também remova as propriedades não usadas `_delegated_handler` e `_resolved_job`.

### REF-3 — Estado compartilhado do agregador vaza
**Arquivo**: [aggregator.py L108, L304-331, L410](file:///c:/codes/ia_candidata/automation/handlers/aggregator.py#L108)
**Problema**: `_delegated[job.id]` é preenchido no `inspect`, mas só é removido em `fill_and_submit`. Se o pipeline retornar antes (DP3, pergunta obrigatória sem resposta, exceção), a entrada fica para sempre e é reutilizada numa retentativa.
**Como fazer**: adicione ao `BaseATSHandler` um hook opcional `def release(self, job: Job) -> None: pass`; o agregador implementa `self._delegated.pop(job.id, None)`. No `Pipeline.process_job`, envolva a Etapa 4-6 num `try/finally: handler.release(job)`.

### REF-4 — Fechar recursos HTTP e aquecer o Laya
**Arquivo**: [main.py](file:///c:/codes/ia_candidata/main.py#L217-L260)
**Problema**: `Matcher` e `LayaSidecarClient` criam `httpx.AsyncClient` que nunca são fechados (`aclose()` existe, mas não é chamado). `LayaSidecarClient.warmup()` nunca é chamado, então o modo degradado (CPU) não ajusta o timeout.
**Como fazer**: use `contextlib.AsyncExitStack` em `run_harness`:
```python
async with AsyncExitStack() as stack:
    matcher = Matcher(settings=settings)
    stack.push_async_callback(matcher.aclose)
    if decision_model is not None:
        stack.push_async_callback(decision_model.aclose)
        await decision_model.warmup()
    browser_mgr = await stack.enter_async_context(BrowserManager(settings))
    ...
```

### REF-5 — Encerramento gracioso (Ctrl+C) sem perder resultados
**Arquivos**: [main.py](file:///c:/codes/ia_candidata/main.py#L260), [core/queue_manager.py](file:///c:/codes/ia_candidata/core/queue_manager.py#L140-L142)
**Problema**: se a execução for interrompida, `results` nunca chega a `write_results`; só o JSONL tem o progresso.
**Como fazer**: em `QueueManager.run`, capture `asyncio.CancelledError`, cancele as tasks pendentes, aguarde `gather(..., return_exceptions=True)` e devolva `list(self.results)`. Em `main.py`, envolva o passo 8 em `try/finally` para sempre executar os passos 9-11 com o que houver em `queue_mgr.results`.

### REF-6 — Aplicar `TASK_TIMEOUT_S` (setting não utilizada)
**Arquivo**: [core/queue_manager.py](file:///c:/codes/ia_candidata/core/queue_manager.py#L59)
**Problema**: `TASK_TIMEOUT_S` existe mas nunca é usado; uma página travada segura um slot do semáforo indefinidamente.
**Como fazer**: `result = await asyncio.wait_for(self.pipeline.process_job(...), timeout=self.settings.TASK_TIMEOUT_S)`. Trate `asyncio.TimeoutError` como `NEEDS_REVIEW` (não `TransientError`), pois o timeout pode ter ocorrido depois do clique em submit — **não re-tentar**.

### REF-7 — Settings mortas ou inconsistentes
**Arquivo**: [config/settings.py](file:///c:/codes/ia_candidata/config/settings.py)
- `FIT_GATE_ENABLED` não é lido em lugar nenhum → aplique em `DecisionLayer.decide_job_fit` (se `False`, `sheet_action = "PROCEED"` sempre).
- `LAYA_MODEL` não é usado → envie no payload de `/ask` se o sidecar aceitar; caso contrário, remova da classe e do `.env.example`.
- `ensure_dirs()` nunca é chamado → chame logo após `load_settings` em `main.py`.
- Adicione validadores: `LLM_MAX_RETRIES >= 1`, `TASK_TIMEOUT_S > 0`, `RESUME_MATCH_THRESHOLD in (0, 1]`.
- Em `main.py`, troque os `getattr(settings, "GOOGLE_...", ...)` (L291-292) por acesso direto.

### REF-8 — Simplificar `Pipeline.process_job`
**Arquivo**: [core/pipeline.py](file:///c:/codes/ia_candidata/core/pipeline.py)
**Problema**: método de ~220 linhas; o bloco de screenshot `try/except: pass` está repetido 4×; o filtro de status prévio (L59-67) duplica o que `load_jobs(only_todo=True)` já faz.
**Como fazer**:
- Crie `async def _review(self, page, job, reason, resume_info, tag, submitted=False) -> ApplicationResult` que tira screenshot (logando falha em `debug`, não `pass` silencioso) e monta o `ApplicationResult`.
- Quebre em métodos privados: `_triage`, `_resolve_resume`, `_build_answers`, `_check_required_facts`.
- O fallback `base_answers` (L208-213) usa `bk.lower() in q.label.lower()` — substring frágil (ex.: chave `"ano"` casa com `"plano"`). Normalize ambos com `core.decision.rules._normalize` e compare por **tokens inteiros**; exija que todos os tokens da chave estejam no rótulo.
- `ready_check` envia `dry_run=False` fixo a `decide_ready_to_submit` → em dry-run o DP6 roda e pode barrar o resultado `COMPLETED`. Passe `dry_run=self.settings.DRY_RUN` e, se quiser auditoria em dry-run, faça `decide_ready_to_submit` gravar o registro de auditoria **antes** do `return` de dry-run (hoje retorna sem auditar).
- `filled` passado ao DP6 não inclui respostas EEO preenchidas pelo handler → perguntas sensíveis obrigatórias aparecem como faltando. Exclua do cálculo do DP6 as perguntas marcadas como sensíveis no DP4 (guarde o conjunto de IDs sensíveis).

### REF-9 — `asyncio.get_event_loop()` dentro de corrotinas
**Arquivo**: [core/decision/layer.py](file:///c:/codes/ia_candidata/core/decision/layer.py) (L171, 181, 269, 280, 329, 339, 407, 417, 472, 483, 544, 554)
**Problema**: uso depreciado; e o padrão "medir latência + chamar Laya + capturar `DecisionUnavailable`" está repetido 6×.
**Como fazer**: crie
```python
async def _timed(self, coro) -> tuple[Any | None, float]:
    start = time.perf_counter()
    try:
        return await coro, (time.perf_counter() - start) * 1000
    except DecisionUnavailable as exc:
        log.warning("Laya indisponível: %s", exc)
        return None, (time.perf_counter() - start) * 1000
```
e use em todos os DPs. Crie também `_noul_probs(v) -> dict | None` para o dicionário `{"true":..., "false":...}` repetido.

### REF-10 — Imports faltando/não usados (lint)
- [automation/browser.py](file:///c:/codes/ia_candidata/automation/browser.py#L9): `Any` é usado mas não importado (funciona só por causa de `from __future__ import annotations`). Adicione `from typing import Any`; troque `typing.AsyncIterator` por `collections.abc.AsyncIterator`.
- [storage/sheet_writer.py](file:///c:/codes/ia_candidata/storage/sheet_writer.py#L112): `Any` não importado.
- Remova imports não usados (`Path`, `Any`, `Locator`, `BlockerDetected`, `asyncio` etc.) apontados pelo `ruff` após QA-2.
- `storage/drive_client.py::DriveClient.__init__` detecta `Settings` via `hasattr` → crie `@classmethod from_settings(cls, settings)` e mantenha `__init__(service, folder_id, cache_dir)` puro. Atualize `main.py` e `scripts/`.

---

## 6. ⚪ Testes e infraestrutura

### QA-1 — Separar e completar dependências
**Arquivo**: [requirements.txt](file:///c:/codes/ia_candidata/requirements.txt)
- `camoufox` é importado em `automation/browser.py` mas não está listado → adicione `camoufox[geoip]>=0.4` (opcional; o código já tem fallback).
- Mova `pytest*`, `respx`, `aiohttp` para `requirements-dev.txt` (com `-r requirements.txt` na 1ª linha) e adicione `ruff` e `mypy`.
- Verifique se `python-dotenv` é usado (pydantic-settings já lê `.env`); se não, remova.
- O arquivo tem encoding quebrado no comentário `# Produção` — salve em UTF-8.

### QA-2 — Configurar `ruff` e `mypy`
Crie `pyproject.toml` com:
```toml
[tool.ruff]
line-length = 120
target-version = "py311"
extend-exclude = ["cache", "artifacts", ".venv"]
[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "ASYNC", "SIM"]
[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
plugins = ["pydantic.mypy"]
```
Rode `ruff check . --fix` e corrija o restante manualmente. Mypy: corrija ao menos `core/` e `config/`.

### QA-3 — Remover scripts de depuração de `cache/`
**Pasta**: `cache/` contém `test_*.py`/`inspect_lever.py` que não são testes pytest e ficam no diretório de dados (e são ignorados pelo git).
**Como fazer**: mova os úteis para `scripts/dev/` (adicione `scripts/dev/README.md` com 1 linha por script) e apague o resto. Nada em `cache/` deve ser código.

### QA-4 — Testes faltantes prioritários
Adicione em `tests/unit/` (todos sem rede/browser):
- `test_resume_resolver.py`: BUG-1, BUG-2, BUG-5.
- `test_pipeline.py`: BUG-6 (descrição propagada), REF-3 (`release` chamado em retorno antecipado), DP6 respeita dry-run.
- `test_decision_layer.py`: BUG-7 (empresa "Cloudflare" não bloqueia), `FIT_GATE_ENABLED=False`.
- `test_laya_client.py`: BUG-9, BUG-10 (`settings=None`), BUG-11 (half-open).
- `test_queue_manager.py`: REF-6 (timeout → `NEEDS_REVIEW` sem retry), REF-5 (cancelamento preserva resultados).
- `test_sheet_writer.py`: BUG-8 (vagas com status vazio são gravadas).
- `test_handlers_finalize.py`: SAFE-3 (botão ausente → `submitted=False`), SAFE-6 (checkbox obrigatório desmarcado).

### QA-5 — Documentação
- Crie `README.md` na raiz: instalação (`venv`, `pip install -r requirements.txt`, `playwright install chromium`), configuração (`.env`, `data/profile.json` a partir do exemplo), autenticação Google (`scripts/auth_google.py`), execução (`python main.py --limit 3`), flags (`--no-dry-run`, `--backend rules`), e como gerar a avaliação do Laya (`python scripts/eval_decisions.py`).
- Adicione ao fim de `docs/PLAN.md`, na seção `## Log`, uma linha por grupo de tarefas concluído.

---

## 7. Checklist de execução

- [x] SEC-3 `.gitignore` + `data/profile.example.json`
- [x] SEC-4 OAuth não interativo no fluxo principal
- [x] BUG-1 `log` em `resume_resolver`
- [x] BUG-2 só `.pdf` indexado
- [x] BUG-3 caminho do relatório de avaliação
- [x] BUG-4 métricas de avaliação corrigidas
- [x] BUG-5 fallback usa apenas currículos-base
- [x] BUG-6 descrição da página propagada
- [x] BUG-7 DP3 com `page_signals`
- [x] BUG-8 `write_results` recebe `jobs`
- [x] BUG-9 parsing robusto em `choice`
- [x] BUG-10 `LayaSidecarClient` sem settings
- [x] BUG-11 breaker half-open
- [x] BUG-12 retries do `Matcher`
- [x] SAFE-1 sem opção arbitrária no Lever
- [x] SAFE-2 sem valores fabricados
- [x] SAFE-3 `PostSubmitError` só após clique
- [x] SAFE-4 agregador exige confirmação
- [x] SAFE-5 agregador valida obrigatórios/EEO
- [x] SAFE-6 checkbox/radio/file nos obrigatórios
- [x] REF-1 helpers no `BaseATSHandler`
- [x] REF-2 sem hacks de mock
- [x] REF-3 `release()` no handler
- [x] REF-4 `AsyncExitStack` + warmup
- [x] REF-5 Ctrl+C preserva resultados
- [x] REF-6 `TASK_TIMEOUT_S` aplicado
- [x] REF-7 settings mortas
- [x] REF-8 pipeline enxuto
- [x] REF-9 `_timed` no DecisionLayer
- [x] REF-10 imports/lint + `DriveClient.from_settings`
- [x] QA-1 requirements
- [x] QA-2 ruff/mypy
- [x] QA-3 limpar `cache/`
- [x] QA-4 testes novos
- [x] QA-5 README

## 8. Critério de pronto

1. `.\.venv\Scripts\python.exe -m pytest -q` verde.
2. `ruff check .` sem erros.
3. `python main.py --limit 1 --backend rules` roda em dry-run sem exceção (com `data/vagas.csv` local).
4. Relatório final ao usuário listando: tarefas feitas, tarefas puladas (com motivo) e arquivos com backup em `artifacts/backup/`.

---

## 9. Auditoria, Blindagem e Refatoração (Outubro/2026)

Execução integral do plano de auditoria, correção de vulnerabilidades e refatoração em 5 fases sequenciais com validação contínua de testes.

### Fase 1: Higiene de Repositório, Codificação e Documentação
- [x] **Encoding UTF-8**: Corrigido docstring em `web/__init__.py` e garantido UTF-8 sem erros em todos os arquivos `.py`.
- [x] **Eliminação de duplicação `OKF/`**: Removida a pasta raiz duplicada `OKF/`, mantendo a canônica em `docs/okf/` referenciada por `docs/OKF_SPECIFICATION.md` e `README.md`.
- [x] **Unificação de dependências**: `pyproject.toml` atualizado com dependências completas do projeto (`fastapi`, `uvicorn`, etc.) e opcionais `[project.optional-dependencies]` para `dev` e `laya`. `requirements.txt` sincronizado.
- [x] **Otimização de ativos**: Imagem `web/static/assets/brand_emblem.jpg` re-comprimida de 695 KB para ~74 KB (~90% de redução).
- [x] **Script de inicialização Linux**: Criado `iniciar_painel.sh` com permissão executável (`chmod +x`), equivalente funcional ao `iniciar_painel.bat`.

### Fase 2: Segurança e Blindagem da Camada Web
- [x] **Proteção de segredos**: Mascaramento estrito de chaves de API (`LLM_API_KEY`) no endpoint `/api/config` e no retorno de `raw_env` (`••••••••`), com permissões restritas `0o600` em arquivos de credenciais/cookies em disco. `storage_state.json` incluído no `.gitignore`.
- [x] **Hardening de API e Frontend**: Servidor restrito por padrão a conexões de `127.0.0.1` / `localhost` via CORS regex; validação de segurança contra Path Traversal em screenshots e seleção de planilhas; sanitização de URLs e links contra XSS em `web/static/app.js`.
- [x] **Controle de concorrência**: Implementado `threading.Lock()` atômico em `web/runner.py` impedindo execuções concorrentes acidentais por múltiplos cliques rápidos na interface web, com cancelamento seguro via SIGTERM/SIGKILL.

### Fase 3: Resiliência do Pipeline, Decisão e Integrações
- [x] **Cliente Laya blindado**: Timeouts explícitos em todas as chamadas HTTP e captura defensiva de JSON malformado ou truncado em `core/decision/laya_client.py` com fallback seguro para `DecisionUnavailable`.
- [x] **Google Drive e Sheets**: Tratamento de erros de rede transitórios e limites de taxa (HTTP 429 / 503) com retry e backoff exponencial em `storage/drive_client.py` e `storage/sheet_loader.py`.
- [x] **Persistência segura em `sheet_writer.py`**: Proteção contra falhas de escrita e tratamento de `PermissionError` (arquivo aberto no Windows) com gravação em arquivo de fallback sem perda de dados na memória.
- [x] **Leitura de PDF robusta**: `core/pdf_text.py` com tratamento de arquivos corrompidos, vazios ou protegidos por senha, lançando `PdfTextError` limpo sem abortar o pipeline.
- [x] **Fila de vagas com concorrência e idempotência**: `core/queue_manager.py` com controle atômico `threading.Lock()` e rastreamento de IDs processados e em voo (`_processed_job_ids`, `_in_flight_job_ids`).

### Fase 4: Estabilidade de Automação e Handlers
- [x] **Ciclo de vida do navegador**: `BrowserManager` com rastreamento de contextos abertos (`_contexts`) e encerramento garantido de páginas e contextos em blocos `finally` e no método `stop()`.
- [x] **Validação canônica de URLs**: Método estático `BaseATSHandler.validate_job_url(url)` integrado a `registry.py` e aos handlers, validando esquemas HTTP/HTTPS antes de qualquer navegação.
- [x] **Correção do falso positivo em confirmação**: Em `BaseATSHandler._finalize`, verificação de `submit_still_active` (evita declarar confirmed se o botão de submit ainda está ativo na tela sem elementos de confirmação), além de ampliação do dicionário de palavras-chave de sucesso.
- [x] **Substituição de timeouts fixos**: Esperas estáticas baseadas em `wait_for_timeout` substituídas por esperas reativas de estado de DOM (`wait_for(state='visible')`, `wait_for_selector`, `wait_for_load_state`) em `greenhouse.py`, `lever.py`, `workable.py` e `indeed.py`.

### Fase 5: Consolidação de Testes e Limpeza de Scripts
- [x] **Cobertura de testes para handlers**: Criados `tests/unit/test_lever_handler.py`, `tests/unit/test_greenhouse_handler.py` e `tests/unit/test_workable_handler.py` cobrindo `can_handle`, detecção de blockers (CAPTCHA), `inspect` e `fill_and_submit` (dry-run e missing fields).
- [x] **Testes parametrizados de agregadores**: Casos de teste para portais Ashby (systemfield selectors), Quickin, Sólides (apply button) e Kognit consolidados em `tests/unit/test_aggregator_handler.py`.
- [x] **Limpeza de `scripts/dev/`**: 13 scripts pontuais com URLs/dados hardcoded movidos para `scripts/dev/archive/`; utilitários ativos (`inspect_lever.py`, `test_gemini.py`, `test_single_job.py`, `test_captcha_vis.py`) documentados em `scripts/dev/README.md`.
- [x] **Validação final da suíte**: Suíte completa de testes passando 100% sem regressões.
