# PLAN — job-harness (harness de candidaturas em lote)

> Raiz do projeto = `c:\codes\ia_candidata` (faz o papel de `job-harness/`). Python 3.11.9.

## 1. Resumo e escopo

Harness assíncrono que lê a planilha "Central de Vagas e Candidaturas" (`.xlsx`/`.csv`), sincroniza do Google
Drive o currículo otimizado de cada vaga para `cache/resumes`, processa as vagas "A Fazer" numa fila com
concorrência limitada, decide pontos tipados (fit, currículo, bloqueio, sensibilidade, mapeamento, envio) com
regras determinísticas + Laya (sidecar `laya-mcp`, consultivo e auditado), usa um LLM compatível com OpenAI só para
texto aberto e desempate, preenche formulários Lever/Greenhouse com Playwright (dry-run por padrão) e grava o
resultado numa **cópia** `.xlsx` da planilha. Toda a suíte roda offline.

**Fora do escopo:** escrita direta no Google Sheets; executar o modelo Laya dentro do processo (só via HTTP);
contornar CAPTCHA/login; outros ATS além de Lever e Greenhouse; ajuste fino do Laya; fit de calibração automático
(o script de avaliação apenas exporta exemplos rotulados para o `CalibrationStore` do pacote).

## 2. Decisões de arquitetura

| decisão | alternativa descartada | motivo |
|---|---|---|
| Laya como sidecar HTTP (`laya-mcp serve`), cliente `httpx` em `core/decision/laya_client.py` | importar o pacote/torch no processo | modelo carregado uma vez e quente; suíte sem torch; falha do Laya isolada do pipeline |
| Fila `asyncio.Queue` + `asyncio.Semaphore(MAX_CONCURRENCY)` | `ThreadPool`/Celery | I/O-bound (browser, HTTP); um processo; testável com `sleep` injetado |
| Planilha como fonte das vagas (`storage/sheet_loader.py`) | `data/jobs.json` | é a fonte de verdade do usuário; já traz fit, currículo e stack |
| Currículo resolvido por nome (`core/resume_resolver.py`, normalização + `difflib`) | LLM escolhe sempre | a planilha já indica o currículo; determinístico e auditável; LLM só no fallback DP2 |
| Modos `off/shadow/active` por DP, com rebaixamento por avaliação | Laya sempre ativo | checkpoints-base perto do acaso; DPs que liberam ação exigem avaliação própria |
| `asyncio.Lock` no cliente + `--concurrency 1` no servidor | chamadas paralelas | Laya não é thread-safe; com concurrency>1 o servidor responde 429 |
| Faixa (`band`) recalculada localmente a partir de `p_true` | usar `band` do servidor | servidor usa 0.30/0.70 fixos; o harness usa limiares próprios (0.2/0.8) |
| Noul sempre enviado com `criteria={"true":…, "false":…}` | noul sem critérios | verificado no código: noul sem critérios responde "false" 40/40; com critérios o servidor o transporta como choice A/B |
| Order-swap no `choice` (2 chamadas, ordem invertida) | 1 chamada | mitiga viés de posição; divergência ⇒ `order_consistent=False`, `margin=0` |
| Drive via backend fino (`files().list/get_media/export_media().execute()`) em `asyncio.to_thread` | `MediaIoBaseDownload` | currículos são pequenos; trivial de simular em teste |
| Resultado escrito em cópia `.xlsx` (`storage/sheet_writer.py`) | editar a original / Sheets API | princípio: original nunca é modificada |
| Auditoria JSONL com `state_sha256` | logar estado bruto | sem PII/texto bruto nos artefatos |

## 3. Suposições e riscos

| # | suposição | risco | verificação | status |
|---|---|---|---|---|
| S1 | **Formato do sidecar Laya** (lido do código de `laya_mcp-0.2.2`: `server.py`, `protocol.py`, `worker.py`, `planning.py`, `errors.py`): `POST /ask` → `{"ok":true,"answers":{qid:{"question_id","type","noul"?:float,"choice"?:str,"probabilities"?:{rótulo:float},"confidence","band"?,"act_probability"?}},"truncated"?:{"state"?:{…},"options"?:{…}},"budget":{plan},"device","degraded"?:true,"warnings"?:[…],"latency_ms","model","routing","usage","confidence_semantics"}`. `POST /plan` → `{"ok":true,"checkpoint","max_len","head_max_len","fits":bool,"questions":[{"question_id","option_count","options_compressed":bool,"would_truncate_state":bool,…}],"state_chars","warnings","recommendation"}`. `GET /health` → `{"ok":true,"loaded","degraded":bool,"calls","failures","checkpoints":{nome:{"device",…}}}`. Erros → `{"ok":false,"error":<code>,"message","retryable",…}` com 400 (`invalid_question`,`question_too_large`,`unknown_model`,`state_truncated`), 429 (`capacity`), 500/503 | mudança de formato numa versão alfa | parse tolerante (`extra="allow"`) isolado em `laya_client.py`; fixtures em `tests/fixtures/laya/` montadas a partir desse código; teste `laya_live` opcional contra sidecar real | verificada (código-fonte 0.2.2); a verificar contra sidecar vivo pelo usuário |
| S2 | Nomes de checkpoint: `english`, `multilingual`, `typed-decisions` (`cli.py`). Calibração: `serve --calibration <store.json>`; fit via API Python `laya_mcp.calibration.fit_from_examples` + `CalibrationStore.save()` (não há subcomando CLI de fit) | nome muda | `laya-mcp serve --help`; `warmup()` avisa se `LAYA_MODEL` não está em `/health.checkpoints` | verificada (código-fonte) |
| S3 | Rótulos do `choice` voltam como chaves de `probabilities` iguais às enviadas | encurtamento de rótulos pelo servidor | cliente casa por igualdade, depois por prefixo único; senão `DecisionUnavailable("unreadable")` | a verificar na Parte 2 (teste) e no `laya_live` |
| S4 | Layout da planilha: título + linhas em branco, cabeçalho na linha 4, coluna A vazia, colunas com os nomes exatos do enunciado; `.csv` em UTF-8 (com/sem BOM), delimitador `,` ou `;` | layout diferente | detecção do cabeçalho por `Empresa`+`Cargo`; `SheetFormatError` se ausente | verificada contra o enunciado; a verificar com o arquivo real na Parte 4 |
| S5 | Currículos no Drive: PDFs **ou** Google Docs numa pasta única (`DRIVE_FOLDER_ID`), nome do arquivo ≈ rótulo da coluna `Currículo Otimizado`; currículos-base têm o nome da base (`Cognition`, `Capco`, `Clara`, `Base Português`, `Base Inglês`) e não começam com os prefixos de otimizado | nomes fora do padrão | `resume_resolver` com normalização + difflib; ambiguidade ⇒ `NEEDS_REVIEW` | a verificar na Parte 4 (execução real) |
| S6 | Laya perto do acaso zero-shot (upstream: 0.362 vs baseline 0.461; multilingual sem temperaturas) | decisões ruins | DP1/DP2/DP5 em `shadow` até `scripts/eval_decisions.py` aprovar com ≥50% de exemplos reais | verificada (docstring de `calibration.py`) |
| S7 | Vagas reais em Lever/Greenhouse aceitam `get_by_label`/`name` estáveis | seletores mudam | fixtures HTML aproximadas; seletores em dataclass por handler | a verificar na Parte 3 (fixtures) / uso real |
| S8 | LLM compatível com OpenAI aceita `response_format={"type":"json_object"}` | provedor não suporta | saída validada por Pydantic com retry; o campo é ignorável pelo provedor | a verificar na Parte 1 (teste com respx) |

## 4. Perguntas em aberto

Nenhuma bloqueante. Suposições padrão adotadas:
1. Nome exato dos arquivos-base no Drive? **Padrão:** o nome contém o nome da base (S5).
2. `LAYA_MODEL` deve ser enviado no corpo (`"model"`) para fixar o checkpoint? **Padrão:** não (é informativo, como no
   enunciado); o `warmup()` só avisa se o checkpoint carregado for outro.
3. Linhas com `Currículo Otimizado = #ERROR!`: idioma? **Padrão:** "en" se `Base Utilizada` contiver "Base Inglês",
   senão "pt".

## 5. Rastreabilidade

| requisito | módulo | teste que comprova |
|---|---|---|
| `DRY_RUN=True` por padrão; precedência CLI > env > default | `config/settings.py`, `main.py` | `test_settings::test_defaults`, `test_settings::test_env_overrides_default`, `test_pipeline::test_cli_overrides_env` |
| Obrigatórias sem default | `config/settings.py` | `test_settings::test_missing_required_raises` |
| Sem invenção de dados (`null`) | `core/matcher.py`, `core/pipeline.py` | `test_matcher::test_null_preserved`, `test_pipeline::test_required_null_needs_review` |
| EEO nunca vai a LLM/Laya | `core/decision/rules.py`, `layer.py`, `pipeline.py` | `test_matcher::test_eeo_not_in_payload`, `test_pipeline::test_sensitive_answered_from_profile_only`, `test_decision_rules::test_sensitive_keywords` |
| CAPTCHA/login ⇒ `NEEDS_REVIEW` | `automation/base_handler.py`, `layer.py` (DP3) | `test_handlers::test_captcha_blocks`, `test_pipeline::test_blocker_needs_review` |
| Conteúdo da vaga delimitado / não confiável | `core/matcher.py` | `test_matcher::test_job_text_delimited` |
| Laya consultivo, modos, rebaixamento | `core/decision/layer.py` | `test_decision_layer::test_shadow_*`, `test_decision_layer::test_demotion_*` |
| Assimetria de segurança DP3/DP4/DP6 | `core/decision/layer.py` | `test_decision_layer::test_invariant_*` |
| Indisponibilidade do Laya ⇒ fallback | `laya_client.py`, `layer.py` | `test_laya_client::test_breaker_*`, `test_decision_layer::test_unavailable_fallback` |
| Auditoria sem PII (hash) | `layer.py`, `states.py` | `test_decision_layer::test_audit_hash_only`, `test_decision_states::test_no_pii` |
| Segredos fora do código/log | `config/settings.py` (`SecretStr`), `.gitignore` | `test_settings::test_secret_not_in_repr` |
| Seletores determinísticos / upload `set_input_files` | `automation/handlers/*` | `test_handlers::test_fills_basic_fields`, `test_handlers::test_upload` |
| Sem envio duplicado | `core/queue_manager.py` | `test_queue_manager::test_no_retry_after_post_submit` |
| Planilha original intacta | `storage/sheet_writer.py` | `test_sheet_writer::test_source_untouched` |
| DP1 `job_fit` | `layer.py`, `states.py` | `test_decision_layer::test_dp1_*` |
| DP2 `resume_fallback` | `layer.py`, `matcher.py` | `test_decision_layer::test_dp2_*`, `test_pipeline::test_missing_resume_uses_dp2_then_llm` |
| DP3 `blocker_check` | `layer.py`, `rules.py` | `test_decision_layer::test_dp3_*` |
| DP4 `question_sensitivity` | `layer.py`, `rules.py` | `test_decision_layer::test_dp4_*` |
| DP5 `field_mapping` | `layer.py`, `rules.py` | `test_decision_layer::test_dp5_*`, `test_pipeline::test_dp5_skips_llm` |
| DP6 `ready_to_submit` | `layer.py`, `rules.py` | `test_decision_layer::test_dp6_*`, `test_handlers::test_ready_check_rejects` |
| Coluna `Data` | `sheet_loader.py` | `test_sheet_loader::test_basic_fields` |
| Coluna `Empresa` / `Cargo` (cabeçalho, id) | `sheet_loader.py` | `test_sheet_loader::test_header_detected`, `test_sheet_loader::test_ids_stable_unique` |
| Coluna `Modalidade` | `sheet_loader.py` | `test_sheet_loader::test_basic_fields` |
| Coluna `Link da Vaga` (hiperlink, "Ver Vaga", `#ERROR!`) | `sheet_loader.py` | `test_sheet_loader::test_xlsx_hyperlink`, `test_sheet_loader::test_bad_link_is_none` |
| Coluna `Currículo Otimizado` (idioma, `#ERROR!`) | `sheet_loader.py` | `test_sheet_loader::test_language`, `test_sheet_loader::test_error_resume_label` |
| Coluna `Fit Score` | `sheet_loader.py` | `test_sheet_loader::test_fit_score_parsing` |
| Coluna `Base Utilizada` | `sheet_loader.py` | `test_sheet_loader::test_lists_split` |
| Coluna `Stack Principal` | `sheet_loader.py` | `test_sheet_loader::test_lists_split` |
| Coluna `Status da Candidatura` (filtro/escrita) | `sheet_loader.py`, `sheet_writer.py` | `test_sheet_loader::test_status_filter`, `test_sheet_writer::test_status_mapping` |

## 6. Plano por parte

### Parte 1 — fundação, storage, matcher
Ordem: `requirements*.txt`, `pytest.ini`, `.gitignore`, `.env.example` → `core/models.py` → `config/settings.py` +
teste → `sheet_loader` (fixture CSV + teste primeiro) → `sheet_writer` → `pdf_text` → `resume_resolver` →
`drive_client` → `matcher`. Dependências: tudo depende de `core/models.py`; `sheet_writer` usa `sheet_loader`.
- [x] `requirements.txt`, `requirements-laya.txt`, `pytest.ini`, `.gitignore`, `.env.example`
- [x] `config/settings.py` — [x] `tests/unit/test_settings.py`
- [x] `core/models.py`
- [x] `storage/sheet_loader.py` — [x] `tests/fixtures/vagas_sample.csv`, [x] `tests/unit/test_sheet_loader.py`
- [x] `storage/sheet_writer.py` — [x] `tests/unit/test_sheet_writer.py`
- [x] `storage/drive_client.py` — [x] `tests/unit/test_drive_client.py`
- [x] `core/pdf_text.py` — [x] `tests/unit/test_pdf_text.py`
- [x] `core/resume_resolver.py` — [x] `tests/unit/test_resume_resolver.py`
- [x] `core/matcher.py` — [x] `tests/unit/test_matcher.py`
- [x] `tests/conftest.py` (marcadores por diretório, `sample_resume_pdf`, `settings`)
- Pronto: `pytest tests/unit -m unit -n auto` verde.

### Parte 2 — camada de decisão
Ordem: `decision/models.py` → `rules.py` + teste → `states.py` + teste → `evaluation.py` + teste →
fixtures `tests/fixtures/laya/*.json` → `laya_client.py` + teste → `FakeDecisionModel` em `conftest.py` →
`layer.py` + teste → `data/decision_goldens.json` → `scripts/eval_decisions.py`.
- [x] `core/decision/models.py`
- [x] `core/decision/rules.py` — [x] `tests/unit/test_decision_rules.py`
- [x] `core/decision/states.py` — [x] `tests/unit/test_decision_states.py`
- [x] `core/decision/evaluation.py` — [x] `tests/unit/test_decision_evaluation.py`
- [x] `core/decision/laya_client.py` — [x] `tests/fixtures/laya/*.json` + `README.md`, [x] `tests/unit/test_laya_client.py`
- [x] `core/decision/layer.py` — [x] `FakeDecisionModel`, [x] `tests/unit/test_decision_layer.py`
- [x] `data/decision_goldens.json` (≥12 sintéticos por DP), [x] `scripts/eval_decisions.py`
- Pronto: `pytest tests/unit -m unit -n auto` verde (62 testes passando).

### Parte 3 — automação
Ordem: fixtures HTML → `browser.py` → `base_handler.py` → `handlers/lever.py`, `handlers/greenhouse.py` →
`registry.py` + teste → `tests/integration/conftest.py` → `test_browser_io.py` → `test_handlers.py`.
- [x] `automation/browser.py` — [x] `tests/integration/test_browser_io.py`
- [x] `automation/base_handler.py`, [x] `automation/handlers/lever.py`, [x] `automation/handlers/greenhouse.py` — [x] `tests/integration/test_handlers.py`
- [x] `automation/registry.py` — [x] `tests/unit/test_registry.py`
- [x] `tests/fixtures/lever_mock.html`, [x] `tests/fixtures/greenhouse_mock.html`, [x] `tests/integration/conftest.py`
- [x] `tests/integration/test_laya_live.py` (marcador `laya_live`, desligado)
- Pronto: `pytest tests/integration -m integration` verde (14 passaram, 1 skip esperado).

### Parte 4 — orquestração
Ordem: `pipeline.py` + teste → `queue_manager.py` + teste → `main.py` → `data/profile.json`.
- [x] `core/pipeline.py` — [x] `tests/unit/test_pipeline.py`
- [x] `core/queue_manager.py` — [x] `tests/unit/test_queue_manager.py`
- [x] `main.py`, [x] `data/profile.json`
- Pronto: `pytest tests/` verde (91 testes passando); `python main.py --help` funciona.

## 7. Critérios de aceite globais

1. [x] `pytest tests/unit -m unit -n auto`, `pytest tests/integration -m integration` e `pytest tests/` passam offline, sem torch/`laya-mcp`.
2. [x] Nada é enviado sem `--no-dry-run`/`DRY_RUN=False` (`test_settings`, `test_handlers`).
3. [x] Planilha original com hash inalterado (`test_sheet_writer`).
4. [x] EEO nunca chega ao LLM/Laya (`test_matcher`, `test_pipeline`).
5. [x] CAPTCHA/obrigatório faltando ⇒ `NEEDS_REVIEW`, sem contorno (`test_handlers`, `test_decision_layer`, `test_pipeline`).
6. [x] Laya indisponível nunca derruba a execução (`test_laya_client`, `test_decision_layer`, `test_pipeline`).
7. [x] DP1/DP2/DP5 só `active` com avaliação aprovada e recente (`test_decision_layer`).
8. [x] Sem retry após submit (`test_queue_manager`, `test_pipeline`).
9. [x] Auditoria sem PII (`test_decision_layer`, `test_decision_states`).
10. [x] Concorrência respeita `MAX_CONCURRENCY` (`test_queue_manager`).

## Log

- **Parte 1 Concluída:**
  - 35/35 testes unitários passando (`python -m pytest tests/unit -m unit -n auto` em 6.5s com 12 workers).
  - Cobertura da Parte 1: `test_settings`, `test_sheet_loader`, `test_sheet_writer`, `test_drive_client`, `test_pdf_text`, `test_resume_resolver`, `test_matcher`.
  - Desvio planejado antecipado: `core/decision/rules.py` e `core/decision/models.py` foram criados para fornecer `is_sensitive_question` ao `matcher.py` e tipagem ao `FakeDecisionModel`.
- **Parte 2 Concluída:**
  - 62/62 testes unitários passando (`python -m pytest tests/unit -m unit -n auto` em 7.25s com 12 workers).
  - Cobertura da Parte 2: `test_decision_rules`, `test_decision_states`, `test_decision_evaluation`, `test_laya_client`, `test_decision_layer`.
  - Implementado protocolo completo Laya: cliente assíncrono com `/plan` preflight, criteria noul `{"true": ..., "false": ...}`, limiares 0.8/0.2, compensação de viés posicional via order-swap em escolhas, detecção de fallback para CPU com 5x timeout, e circuit breaker.
  - Conjunto sintético de 72 goldens em `data/decision_goldens.json` e script `scripts/eval_decisions.py` com portão de segurança (DP1/DP2/DP5 só ativam com dados reais e ganho sobre o baseline).
- **Parte 3 Concluída:**
  - 14/14 testes de integração passando (`python -m pytest tests/integration -m integration` em 33.3s; 1 skip esperado para sidecar live).
  - Implementados: `automation/browser.py`, `base_handler.py`, `handlers/lever.py`, `handlers/greenhouse.py`, `registry.py`.
  - Fixtures `lever_mock.html` e `greenhouse_mock.html` servidos por servidor dinâmico `aiohttp` com simulação de CAPTCHA, ausência de confirmação pós-submit e tags de analytics bloqueadas via Playwright route aborting.
  - Resiliência no Windows offline: `BrowserManager` suporta fallbacks transparentes de canal (`channel="chrome"` e `channel="msedge"`).
  - Isolamento rigoroso de contexto entre candidaturas e proteção EEO nativa nos formulários ATS.
- **Parte 4 Concluída:**
  - 91/91 testes passando na suíte completa (`python -m pytest tests/ -v` em 40.9s).
  - Implementados: `core/pipeline.py`, `core/queue_manager.py`, `data/profile.json`, `main.py`.
  - `core/pipeline.py` orquestra todas as etapas (fit score gate, DP1 pré-triagem, resolução de currículo DP2, inspeção no ATS e DP3, perguntas do formulário com proteção EEO nativa, verificação DP4/DP6 e preenchimento).
  - `core/queue_manager.py` implementa concorrência assíncrona limitada via Semaphore, retry estritamente em erros transitórios pré-submit e política de bloqueio absoluto de retry pós-submit, além de escrita JSONL thread/corrotina-segura.
- **Suporte a Agregadores de Vagas (AggregatorHandler implementado e validado):**
  - Implementado `automation/handlers/aggregator.py` com detecção de agregadores (Indeed, Remote Rocketship, Remotar, DailyRemote, etc.).
  - Capacidade de bridge/handover inteligente: inspeciona a página do agregador, localiza o botão de candidatura externa e faz o handover para `LeverHandler` / `GreenhouseHandler` automaticamente.
  - Suporte a preenchimento de formulários genéricos/embutidos na página do agregador com upload de currículo e dados do perfil.
  - Detecção rigorosa de bloqueios anti-bot (Cloudflare, CAPTCHA, telas de login), salvando screenshots e marcando como `NEEDS_REVIEW` com motivo explicativo.
  - 84/84 testes unitários passando.
- **Melhorias - Segurança e Invariantes (SEC-3, SEC-4, SAFE-1 a SAFE-6):**
  - SEC-3: `.gitignore` atualizado para proteger tokens, credenciais e profiles reais; `data/profile.example.json` criado como template.
  - SEC-4: OAuth não-interativo no fluxo principal; `scripts/auth_google.py` dedicado para login interativo.
  - SAFE-1: LeverHandler não escolhe primeira opção arbitrária em `<select>` não casado.
  - SAFE-2: Adicionado `current_company` ao `Profile`; remoção de dados fabricados (`last_name="-"`, empresa e comentários fictícios).
  - SAFE-3: `PostSubmitError` nunca disparado antes do clique de envio; botão ou currículo ausente retorna `NEEDS_REVIEW` com `submitted=False`.
  - SAFE-4: AggregatorHandler exige confirmação real de envio via seletor ou lança `PostSubmitError`.
  - SAFE-5: AggregatorHandler valida campos obrigatórios e infere labels para selects de EEO sem vazar dados sensíveis.
  - SAFE-6: Verificação de obrigatórios checa estado real de checkbox (`is_checked()`), grupos de radio e arquivos (`files.length > 0`), ignorando ocultos/desabilitados.
- **Melhorias - Correções de Bugs (BUG-1 a BUG-12):**
  - BUG-1: Logger adicionado a `resume_resolver.py`.
  - BUG-2: Apenas arquivos `.pdf` indexados no resolver de currículos.
  - BUG-3: Caminho do relatório de avaliação resolvido via `settings.LAYA_EVAL_FILE`.
  - BUG-4: Métricas de avaliação estatística corrigidas: selective accuracy e cobertura mínima.
  - BUG-5: Fallback de currículo (DP2) utiliza estritamente currículos-base, ignorando os customizados por vaga.
  - BUG-6: Descrição extraída da página propagada para o `Job` e repassada ao `Matcher`.
  - BUG-7: DP3 utiliza `page_signals` desprovidos de PII/empresa, eliminando falsos positivos na empresa Cloudflare.
  - BUG-8: `write_results` recebe dicionário de vagas `jobs`, garantindo escrita das vagas com status inicial em branco.
  - BUG-9: Parsing robusto em `choice` do sidecar Laya com tratamento estrito de exceções para `DecisionUnavailable`.
  - BUG-10: `LayaSidecarClient` resiliente quando instanciado com `settings=None` com atributos pré-computados.
  - BUG-11: Circuit breaker do Laya implementa estado half-open respeitando `LAYA_BREAKER_COOLDOWN_S`.
  - BUG-12: `Matcher._post` respeita `self._sleep`, `LLM_MAX_RETRIES`, backoff exponencial e cabeçalho `Retry-After`.
- **Melhorias - Refatoração e Qualidade (REF-1 a REF-10):**
  - REF-1: Helpers comuns extraídos para `BaseATSHandler` (`_check_blockers`, `_choose_eeo_option`, `_find_empty_required`, `_finalize`).
  - REF-2: Limpeza de mocks e propriedades desnecessárias em `AggregatorHandler`.
  - REF-3: Adicionado hook `release(job)` no handler e executado em `try ... finally` no `Pipeline.process_job`.
  - REF-4: `AsyncExitStack` em `main.py` fechando clientes HTTP (`Matcher`, `LayaSidecarClient`) e executando `warmup()` no Laya.
  - REF-5: Tratamento gracioso de `CancelledError` no `QueueManager.run` e `try/finally` em `main.py` preservando resultados parciais na planilha.
  - REF-6: Aplicação de `TASK_TIMEOUT_S` com `asyncio.wait_for`; timeout gera `NEEDS_REVIEW` sem retry.
  - REF-7: Limpeza de configurações mortas (`LAYA_MODEL` removido, `FIT_GATE_ENABLED` integrado no DP1, validação de tipos e limites).
  - REF-8: Pipeline refatorado em métodos privados enxutos (`_review`, `_triage`, `_resolve_resume`, `_build_answers`, `_check_required_facts`) com passagem correta de `dry_run`.
  - REF-9: Eliminação de `asyncio.get_event_loop()` obsoleto em `core/decision/layer.py` usando helper assíncrono `_timed`.
  - REF-10: Tipagem rigorosa com `DecisionSource`, cast de navegador e pureza no construtor de `DriveClient.from_settings`.
- **Melhorias - Testes e Infraestrutura (QA-1 a QA-5):**
  - QA-1: `requirements.txt` atualizado (adicionado `camoufox[geoip]>=0.4`, UTF-8) e `requirements-dev.txt` criado.
  - QA-2: `pyproject.toml` configurado para `ruff` e `mypy`. 100% de conformidade com 0 erros de lint e 0 erros de tipo em `core` e `config`.
  - QA-3: Scripts de depuração movidos de `cache/` para `scripts/dev/` com `README.md` explicativo.
  - QA-4: Suíte de testes unitários expandida para 121 testes, todos passando (cobertura total de BUGs, SAFEs e REFs).
  - QA-5: Criação do `README.md` raiz completo e atualização deste log de execução.




