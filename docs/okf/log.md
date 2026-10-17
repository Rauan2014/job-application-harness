---
type: Changelog
title: Candidata IA — Log de Decisões e Evolução do Conhecimento
description: Registro cronológico reservado de decisões arquiteturais e marcos de conhecimento no padrão OKF.
tags:
  - okf
  - log
  - adr
  - history
timestamp: "2026-10-11T23:55:00Z"
---

# Log de Decisões de Arquitetura e Conhecimento (OKF)

Este arquivo reservado registra a evolução das decisões arquiteturais (ADRs), invariantes de projeto e marcos de prontidão para código aberto.

---

## 2026-10-12: Correção de Submissão Real (`dry_run=False`) e Sincronização Completa da Planilha
- **Autor**: Rauan Bernardo Ribeiro de Araujo
- **Contexto**: Investigação e correção de falhas pós-submissão no modo real (`dry_run=False`) e da dessincronização entre a planilha local em cache (`cache/vagas_sheets_latest.xlsx`), a planilha de resultados (`artifacts/vagas_resultado.xlsx`) e a UI.
- **Decisões Adotadas**:
  - **Confirmação Pós-Submissão e Seleção do Botão Submit (`BaseATSHandler._finalize` & `GenericSelectors.SUBMIT_BTN`)**:
    - Exclusão explícita do CTA de rolagem do Inhire (`[data-component-name='go-to-job-application-form']`) dos seletores de submissão (`SUBMIT_BTN`) e busca reversa (bottom-up) para priorizar sempre o `<button type="submit">` do formulário em vez de botões do cabeçalho.
    - Adicionado fallback de clique via JavaScript (`evaluate("el => el.click()")`) e verificação de visibilidade real dos elementos de confirmação e ausência de erros de validação (`.field-error`, `.invalid-feedback`, `.form-error`), eliminando falsos negativos de `PostSubmitError` em formulários SPA (`Kake`, `EPAM`, `Trinca`, `Lever`).
  - **Detecção de Vagas Encerradas (HTTP 404) no Indeed (`IndeedApplyHandler._check_blockers`)**:
    - Verificação do título e corpo da página para identificar páginas 404 / vagas indisponíveis no Indeed (`404 Page not Found`, `não está mais disponível`), classificando-as automaticamente como `SKIPPED` (`Ignorada`) em vez de `NEEDS_REVIEW`.
  - **Correção de Links (`Link da Vaga`) e Sincronização Persistente da Planilha (`storage/sheet_writer.py`, `web/runner.py`, `web/data_provider.py`)**:
    - `write_results()` e `update_google_sheet_results()` passam a gravar também a URL direta resolvida (`job.url` + hiperlink) na coluna `Link da Vaga` (`COL_LINK`).
    - Implementada `sync_sheet_with_local_state()` para consolidar URLs diretas (`_URL_FALLBACKS`), `Status da Candidatura`, `Motivo` e `Processado em` tanto em `cache/vagas_sheets_latest.xlsx` quanto em `artifacts/vagas_resultado.xlsx` antes e depois de cada execução.
    - Detecção automática do nome da plataforma ATS (`Lever`, `Ashby`, `Workable`, `Inhire`, `Quickin`, `Indeed`, `Sólides`, `Gupy`) a partir da URL resolvida em `web/data_provider.py::_detect_ats_label()`.

---

## 2026-10-12: Redesenho da Navbar Superior e Automação Integral de Gargalos de Revisão Humana (`NEEDS_REVIEW`)
- **Autor**: Rauan Bernardo Ribeiro de Araujo
- **Contexto**: Refatoração da barra de navegação superior (`header.navbar`) que apresentava compressão excessiva em múltiplas larguras de tela, seguida de investigação de causa-raiz e automação de todos os gargalos que levavam vagas para `NEEDS_REVIEW` ou `SKIPPED`.
- **Decisões Adotadas**:
  - **Redesenho Estrutural da Navbar (`header.navbar`)**:
    - Layout de 3 zonas em CSS Grid (`minmax(200px, auto) 1fr minmax(260px, auto)`) com pílula central unificada (`nav.navbar-tabs`) para as 5 abas principais.
    - Unificação do badge de status operacional e do cronômetro ao vivo (`00:00:00`) em um único componente compacto (`#globalStatusBadge` + `#headerElapsedTimer`).
    - Movimentação das informações secundárias de diagnóstico (caminho da planilha, contadores de currículos e status da chave de API) para uma barra de contexto inferior dedicada (`.context-subbar`) na aba de Operação, liberando espaço horizontal nobre no cabeçalho.
  - **Eliminação de Falsos Positivos de Anti-Bot (`BaseATSHandler._check_blockers`)**:
    - Ignorados iframes `bframe` ocultos/fora da tela (`x < 0`, `y < 0` ou `< 100x100`) e selos do Google Invisible reCAPTCHA (`data-size="invisible"` ou `iframe[src*='anchor']`), resolvendo falsos bloqueios em portais Inhire (`iconit.inhire.app`, `deal.inhire.app`, `mazzatech.inhire.app`).
  - **Automação de Formulários Multi-Step e Modais (`Workable`, `Inhire`, `Quickin`, `Ashby`, `Kake`, `DreamHub`)**:
    - **Workable (`WorkableHandler`)**: Adicionado suporte aos seletores modernos `[data-ui='overview-apply-now']` e `<dialog open>`, evitando duplo clique que fechava o modal, preenchendo endereço/cidade/país e anexando currículo em todos os campos de documento.
    - **Inhire / Agregadores (`AggregatorHandler`)**: Implementado `_click_intermediate_apply_button()` para clicar apenas no CTA visível (`Candidatar-se para a vaga`), seleção automática do DDI `+55 (Brasil)`, limpeza de máscara numérica para pretensão salarial (`"18000"`), seleção de modelos de trabalho (`workModel`) e aceite de termos LGPD.
    - **Quickin (`AggregatorHandler`)**: Adicionado filtro `_should_skip_file_input()` para impedir o upload do PDF de currículo em campos de foto/imagem (`input#photo`), suporte a rádios binários (`radio-yes-questio-*`) e preenchimento automático de diversidade (`Prefiro não responder`).
    - **Ashby (`AggregatorHandler`)**: Extração de rótulos reais a partir de containers `.ashby-application-form-field-entry` (em vez de UUIDs brutos) e clique automático em botões toggle `<button>Yes</button>` / `<button>No</button>`.
    - **Portais Multi-Hop (`Kake`, `DreamHub -> Remotar -> ATS`)**: Resolução de links externos em múltiplos saltos (`for hop in range(2):`) para atravessar páginas intermediárias até o formulário final.
  - **Refinamento da Camada de Decisão Determinística (`core/decision/rules.py` & `core/matcher.py`)**:
    - Adicionado `min_score=3.0` em `rank_profile_keys()` para impedir que chaves com score `0.0` (como `contract_type` = `"PJ ou CLT..."`) poluam campos não relacionados.
    - Novas regras determinísticas em `resolve_profile_answer()` para perguntas condicionais (`"If the previous answer is Yes..."` -> `"N/A"`), residência no Brasil, disponibilidade híbrida, proficiência binária em inglês, ferramentas/stack atuais e experiência técnica binária.
  - **Deduplicação de Histórico na UI (`web/data_provider.py`)**:
    - `get_results_history()` passa a consolidar múltiplas tentativas por `job_id` mantendo o status mais recente e contabilizando o total de `attempts`, além de incluir vagas com status `"Candidatado"` no cache de metadados (`include_completed=True`).

---

## 2026-10-12: Otimização Integral da Experiência do Usuário (UX/UI para Operadores Leigos)
- **Autor**: Rauan Bernardo Ribeiro de Araujo
- **Contexto**: Auditoria aprofundada de usabilidade para operadores não-técnicos, eliminando atritos e implementando recursos de produtividade.
- **Decisões Adotadas**:
  - Inclusão do **Guia Rápido de 3 Passos** com onboarding acionável e persistência de dispensa no `localStorage`.
  - Cronômetro ao vivo em tempo real (`00:00:00`) com cálculo preditivo de término (ETA) baseado no tempo médio por vaga.
  - Implementação de exportação instantânea para **CSV/Excel** em UTF-8 com BOM para evitar caracteres corrompidos.
  - Contadores dinâmicos `(N)` nas pílulas de filtro e ordenação ascendente/descendente clicável nos cabeçalhos de coluna.
  - Substituição do diálogo nativo `window.prompt` por **Modal Tático de Seleção de Portais** (*Indeed*, *Gupy*, *LinkedIn*).
  - Criação da **Gaveta / Modal de Auditoria Detalhada da Vaga** para inspeção profunda de tokens, raciocínio da IA e evidências.
  - Ferramentas no Console de Logs: *Copiar*, *Limpar*, *Auto-Scroll* e *Filtro de Erros/Avisos*.
  - Menus de presets inteligentes para modelos de IA (`gemini-2.5-flash`, `gpt-4o-mini`, etc.) e botão direto de abertura da planilha Google.
  - Alerta sonoro sintetizado nativo via Web Audio API e atalhos globais de teclado (`Esc`, `/`, `1` a `5`).

---

## 2026-10-12: Identidade Visual Única e Design System "Candidata // Protocol (Studio Edition)"
- **Autor**: Rauan Bernardo Ribeiro de Araujo
- **Contexto**: Eliminação de clichês estéticos de "vibe code" / "AI wrappers" genéricos (emojis infantis, gradientes roxos sem identidade, botões flutuantes descalibrados).
- **Decisões Adotadas**:
  - Conceitualização da marca **Candidata // Protocol (Studio Edition / Quantum Stealth)**: estética de cockpit aeroespacial e telemetria de alta densidade.
  - Implementação de Design System proprietário com tokens CSS (`--bg-void: #06080B`, `--mint: #00F29D`, `--cyan: #38BDF8`, `--amber: #F59E0B`, `--rose: #F43F5E`).
  - Criação de assets vetoriais originais em SVG: logotipo com escudo de abertura quântica (`logo.svg`), favicon em alta definição (`favicon.svg`) e render de estúdio em alta resolução (`brand_emblem.jpg`).
  - Erradicação de emojis em 100% da interface do usuário em favor de micro-vetores SVG geométricos e micro-LEDs de status.
  - Tipografia de alta precisão com números tabulares (`JetBrains Mono`) para telemetria em tempo real (tokens, latência, timestamps) sem oscilação de layout.
  - Documentação formal em `docs/okf/design_system.md`.

---

## 2026-10-12: Implementação da Interface Web (UI) e Métricas em Tempo Real
- **Autor**: Rauan Bernardo Ribeiro de Araujo
- **Contexto**: Criação de painel visual moderno para operação 100% autônoma por usuários não-técnicos, sem dependência de terminal ou IDE.
- **Decisões Adotadas**:
  - Implementação da camada Web em FastAPI (`web.app`) servindo Single-Page Application (SPA) em HTML5/CSS3/JS sem dependências externas em tempo de execução.
  - Streaming em tempo real via Server-Sent Events (SSE) em `/api/events`, eliminando atrasos e polling agressivo.
  - Adição de rastreamento estrito de métricas por vaga no modelo `ApplicationResult`: `duration_s` (latência em segundos), `tokens_used`, `prompt_tokens` e `completion_tokens` capturados diretamente do `Matcher`.
  - Adição de callbacks de granularidade fina `on_step` no `Pipeline` e `QueueManager` para transmissão ao vivo do estágio exato de preenchimento (ex.: *Inspecionando formulário*, *Gerando respostas via IA*, *Preenchendo campos*).
  - Criação de gerenciador visual de cookies (`web.cookie_manager`) com suporte a importação de JSON e acionamento de navegador interativo com 1 clique.
  - Disponibilização do script `iniciar_painel.bat` para execução imediata no Windows com abertura automática do navegador.

---

## 2026-10-11: Criação da Documentação Oficial em Padrão OKF (Google Cloud)
- **Autor**: Rauan Bernardo Ribeiro de Araujo
- **Contexto**: Preparação do projeto para auditoria e publicação em código aberto (Open Source).
- **Ações**:
  - Estruturação do bundle de conhecimento em `docs/okf/` seguindo a especificação v0.2 do Google Cloud.
  - Formalização dos nós do grafo: [index.md](index.md), [architecture.md](architecture.md), [decision_engine.md](decision_engine.md), [security_and_ethics.md](security_and_ethics.md), [data_contract.md](data_contract.md) e [open_source_readiness.md](open_source_readiness.md).
  - Mapeamento e auditoria de 4 bloqueadores críticos de segurança (chave em `.env.example`, credenciais Google, PII em `data/profile.json` e logs em `artifacts/`).

---

## 2026-10-04: Implementação da Camada de Decisão Calibrada e Invariantes
- **Contexto**: Implementação inicial dos pontos de decisão DP1 a DP6 e integração com sidecar Laya-MCP.
- **Decisões Adotadas**:
  - `DRY_RUN=True` fixado como padrão obrigatório em todos os fluxos.
  - Isolamento de perguntas sensíveis (EEO): remoção de qualquer chamada remota a LLM ou ML para tópicos de autodeclaração.
  - Imutabilidade da planilha de entrada: saída salva estritamente em cópia `.xlsx` independente.
  - Introdução do disjuntor (circuit breaker) e fallback determinístico caso o sidecar Laya esteja indisponível.
