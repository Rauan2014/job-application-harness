# Job Application Harness

<p align="left">
  <strong>🇧🇷 Português (PT-BR)</strong> &nbsp;|&nbsp; 🇺🇸 <a href="README.en.md"><strong>Read in English (README.en.md)</strong></a>
</p>

Motor assíncrono de código aberto (*Fair-Code* — SUL 1.0) para **automação ética, calibrada e auditável de candidaturas a vagas de emprego** em plataformas ATS (**Greenhouse, Lever, Workable, Indeed** e agregadores compatíveis). Combina navegação anti-detecção (**Camoufox / Playwright Chromium**), triagem estatística calibrada (**Laya DP1–DP6**), busca ativa diária de vagas, resolução inteligente de currículos em PDF e preenchimento assistido por LLM estritamente ancorado aos fatos verificados do candidato.

---

## ☁️ Candidata Cloud (Versão Gerenciada na Nuvem — Zero Instalação)

> [!TIP]
> **Prefere usar sem instalar Python, navegadores, chaves de API ou editar arquivos JSON no terminal?**
>
> Conheça o **[Candidata Cloud](https://candidata.cloud)** — plataforma web gerenciada construída sobre este motor:
> - 📄 **Importação Inteligente de Currículo**: Envie seu PDF e estruture seu perfil ATS automaticamente.
> - 🤖 **Execução em Nuvem com Comprovantes**: Captura de fotos (screenshots) auditáveis de cada formulário em Modo Ensaio (*Dry-Run*) ou Envio Real.
> - 🛡️ **Trava Anti-Mentiras & Revisão em 1 Clique**: Pausa automática quando uma pergunta inédita exige confirmação humana.

---

## ✨ Principais Recursos

- **Automação Multi-ATS Assíncrona**: Handlers dedicados em `automation/handlers/` para **Greenhouse**, **Lever**, **Workable**, **Indeed** e **Agregadores** (`automation/registry.py`), com controle de concorrência (`MAX_CONCURRENCY`), *backoff* exponencial e bloqueio de rastreadores analíticos.
- **Navegação Stealth ou Padrão**: Suporte nativo ao **Camoufox** (anti-fingerprinting, padrão no `.env`) e ao **Playwright Chromium** (`--browser {camoufox,chromium}`), com persistência de sessão/cookies em `cache/storage_state.json`.
- **4 Modos de Ingestão de Vagas**:
  1. **Busca Ao Vivo Sob Demanda (`--search`)**: Pesquisa nativa de vagas em tempo real e enfileiramento imediato (`core/job_search.py`).
  2. **Caçador Diário Automático (`core/daily_job_hunter.py`)**: Varredura diária ativa (GitHub Tech BR, Jobicy, RemoteOK) cruzada com as *skills* do candidato, com deduplicação e inserção automática na fila (`PENDING`).
  3. **Planilhas Google Sheets / Excel / CSV (`--sheet`)**: Leitura direta (inclusive via link público sem OAuth) e gravação automática dos status atualizados na planilha local e online.
  4. **Fila em Nuvem via Supabase (`--from-supabase` / `cloud_worker.py`)**: Consumo transacional seguro via RPC `worker_claim_next_job` (`FOR UPDATE SKIP LOCKED`).
- **Camada de Decisão Calibrada (DP1–DP6)**: Suporte ao sidecar neural local **Laya** (com validação estatística de calibração ECE) e fallback determinístico instantâneo por regras (`--backend rules`), além de operação **100% local sem LLM externo (`--no-llm`)**.
- **Invariantes Éticos e Segurança por Padrão**:
  - `DRY_RUN=true` por padrão (preenche todo o formulário e gera screenshot comprobatório sem submeter até que `--no-dry-run` seja informado).
  - Isolamento de dados sensíveis/autodeclarações (**EEO / LGPD Shield**).
  - Alternância entre modo autônomo (`--autonomous`) e pausa estrita para revisão humana (`--no-autonomous` → `NEEDS_REVIEW`).
- **Painel Visual Local (`web/`)**: Interface gráfica FastAPI + Server-Sent Events (SSE) em `http://127.0.0.1:8000` para configurar 100% do `.env`, editar perfil, gerenciar cookies e inspecionar screenshots em tempo real.

---

## 🏗️ Arquitetura do Repositório

```text
job-application-harness/
├── main.py                  # CLI principal para execução em lote (planilha, busca ao vivo ou Supabase)
├── cloud_worker.py          # Daemon contínuo de produção integrado à fila do Supabase
├── deploy_vps.sh            # Provisionamento automatizado Ubuntu VPS (Swap, Docker, UFW e Worker)
├── automation/              # BrowserManager (Camoufox/Chromium) e handlers ATS (Greenhouse, Lever, Workable, Indeed...)
├── core/                    # Pipeline, Matcher LLM, busca ao vivo, daily_job_hunter e motor de decisão (Laya/Rules)
├── config/                  # Configurações tipadas via Pydantic Settings (.env)
├── storage/                 # Clientes Google Sheets, Google Drive e Supabase Cloud
├── web/                     # Painel visual local (FastAPI + SSE + editor completo de configurações e sessão)
├── scripts/                 # Utilitários OAuth Google, login_browser, sync_sheet_resumes, avaliação Laya e Task Scheduler
├── data/                    # Exemplo de perfil (profile.example.json) e conjunto de calibração (decision_goldens.json)
└── docs/okf/                # Grafo de documentação técnica no padrão Google Open Knowledge Format (OKF v0.2)
```

---

## 🚀 1. Instalação

### Pré-requisitos
- **Python 3.11+**
- **Git**

### Passo a passo

1. **Clone o repositório:**
   ```bash
   git clone https://github.com/Rauan2014/job-application-harness.git
   cd job-application-harness
   ```

2. **Crie e ative o ambiente virtual:**
   ```bash
   python -m venv .venv
   # Windows (PowerShell):
   .\.venv\Scripts\Activate.ps1
   # Linux / macOS:
   source .venv/bin/activate
   ```

3. **Instale as dependências:**
   ```bash
   pip install -r requirements.txt

   # Opcional — ferramentas de teste, lint e tipagem:
   pip install -r requirements-dev.txt

   # Opcional — sidecar neural Laya:
   pip install -r requirements-laya.txt
   ```

4. **Instale o navegador de automação:**
   ```bash
   playwright install chromium
   # Caso utilize o Camoufox (anti-detect, ativo por padrão):
   python -m camoufox fetch
   ```

---

## ⚙️ 2. Configuração

1. **Variáveis de ambiente (`.env`):**
   ```bash
   # Windows:
   copy .env.example .env
   # Linux / macOS:
   cp .env.example .env
   ```
   Principais variáveis no `.env`:
   - `LLM_API_KEY`: Chave da API compatível com OpenAI (Google AI Studio / Gemini, OpenAI, OpenRouter, Groq).
   - `LLM_BASE_URL`: Endpoint da API (ex.: `https://generativelanguage.googleapis.com/v1beta/openai/`).
   - `LLM_MODEL`: Modelo utilizado (ex.: `gemini-2.5-flash` ou `gpt-4o-mini`).
   - `DRIVE_FOLDER_ID`: ID da pasta do Google Drive com currículos. **Dica:** se seus PDFs já estiverem localmente em `cache/resumes/` sem usar Google Drive, preencha `DRIVE_FOLDER_ID=none`.
   - `GOOGLE_SHEET_URL`: URL completa ou ID da planilha do Google Sheets (se compartilhada como *"Qualquer pessoa com o link pode ler"*, funciona sem credenciais do Google Cloud).
   - `DRY_RUN`: `true` por padrão (modo simulação seguro).

2. **Perfil do candidato (`data/profile.json`):**
   ```bash
   # Windows:
   copy data\profile.example.json data\profile.json
   # Linux / macOS:
   cp data/profile.example.json data/profile.json
   ```
   Preencha seus dados verídicos (nome, e-mail, telefone, localização, links de LinkedIn/GitHub/portfólio, pretensão salarial, respostas base e preferências de autodeclaração EEO/LGPD) e adicione seus currículos `.pdf` em `cache/resumes/`.

3. **Utilitários Opcionais de Autenticação e Sessão:**
   ```bash
   # Salvar sessão/cookies persistentes de portais (ex.: Indeed, LinkedIn, Google) em cache/storage_state.json:
   python scripts/login_browser.py --url https://br.indeed.com

   # Autenticação OAuth Google Drive / Sheets (necessária apenas para pastas ou planilhas privadas):
   python scripts/auth_google.py

   # Baixar automaticamente currículos Google Docs linkados via HYPERLINK na planilha para cache/resumes/:
   python scripts/sync_sheet_resumes.py
   ```

---

## 🖥️ 3. Modos de Uso

### A) Painel Visual Local (Recomendado para Uso Desktop)

Inicie a interface web local em `http://127.0.0.1:8000`:

```bash
python -m web
# Ou dê dois cliques em iniciar_painel.bat (Windows) / ./iniciar_painel.sh (Linux/macOS)
```

Recursos inclusos no painel:
- Stream SSE em tempo real (`/api/events`) de cada etapa (`inspect`, `fill`, `submit`) e decisões DP1–DP6;
- Editor visual completo de todas as variáveis do `.env`, perfil do candidato e cookies de sessão;
- Galeria de evidências visuais (`/api/screenshots`) e métricas de tokens/latência (`/api/metrics`).

### B) Linha de Comando (`main.py`)

```bash
# 1. Simulação segura (dry-run padrão — preenche e tira screenshot sem submeter):
python main.py --limit 3

# 2. Busca nativa de vagas ao vivo + simulação imediata:
python main.py --search "Senior Python Engineer" --limit 5

# 3. Execução a partir de planilha local (.xlsx ou .csv) ou link do Google Sheets:
python main.py --sheet data/vagas.xlsx --limit 5

# 4. Execução 100% determinística por regras e sem chamadas a LLMs externas:
python main.py --limit 3 --backend rules --no-llm

# 5. Exigir revisão humana estrita quando faltar algum dado obrigatório:
python main.py --limit 3 --no-autonomous

# 6. Submissão real nos portais ATS (desativa dry-run):
python main.py --limit 3 --no-dry-run

# 7. Consumir vagas pendentes diretamente do Supabase:
python main.py --from-supabase --limit 10
```

#### Referência de Flags da CLI (`python main.py --help`)

| Flag | Descrição | Padrão |
| :--- | :--- | :--- |
| `--sheet PATH_OR_URL` | Caminho local (`.xlsx`/`.csv`) ou URL/ID do Google Sheets | `GOOGLE_SHEET_URL` ou `data/vagas.xlsx` |
| `--search QUERY` | Realiza busca nativa de vagas ao vivo e enfileira os resultados | Desativado |
| `--from-supabase` | Carrega e sincroniza vagas diretamente da tabela `job_applications` no Supabase | `False` |
| `--profile PATH` | Caminho do JSON de perfil do candidato | `data/profile.json` |
| `--limit N` | Limite máximo de vagas nesta execução | Sem limite |
| `--dry-run` / `--no-dry-run` | Ativa simulação segura (padrão) ou realiza envio oficial no ATS | `--dry-run` (`True`) |
| `--concurrency N` | Número máximo de navegadores simultâneos | `MAX_CONCURRENCY` (`3`) |
| `--backend {laya,rules}` | Motor de decisão: sidecar neural `laya` ou regras determinísticas `rules` | `DECISION_BACKEND` (`laya`) |
| `--browser {camoufox,chromium}` | Motor de navegador: `camoufox` (anti-detect) ou `chromium` | `USE_CAMOUFOX` (`camoufox`) |
| `--no-llm` | Opera 100% local com regras + perfil, sem chamadas a APIs de LLM | `False` |
| `--autonomous` / `--no-autonomous` | Sintetiza respostas obrigatórias automaticamente vs. pausa para revisão humana | `--autonomous` (`True` na CLI) |
| `--sync-drive` | Sincroniza currículos da pasta do Google Drive antes de iniciar | `False` |
| `--include-blank-status` / `--no-include-blank-status` | Inclui ou ignora linhas com status em branco na planilha | `INCLUDE_BLANK_STATUS` (`True`) |
| `--include-review-status` | Inclui vagas marcadas como `Revisar` | `False` |
| `--include-failed` | Reprocessa vagas marcadas como `Falhou` | `False` |
| `--retry-all` (`--all`) | Processa todas as vagas não concluídas (`A Fazer`, `Revisar`, `Falhou` e vazias) | `False` |

### C) Estrutura da Planilha de Vagas (Quando usar `--sheet` / Google Sheets)

| Coluna | Obrigatória? | Exemplo | Descrição |
| :--- | :---: | :--- | :--- |
| **`Link da Vaga`** (ou `URL da Vaga`) | **Sim** | `https://jobs.lever.co/empresa/abc123` | URL direta da página de candidatura. |
| **`Currículo Otimizado`** | **Sim** | `Base Inglês` | Nome do PDF ou rótulo resolvido via *fuzzy matching* em `cache/resumes/`. |
| **`Status da Candidatura`** | **Sim** | `A Fazer` | Filtrado na entrada e atualizado ao fim para `Concluído`, `Revisar` ou `Falhou`. |
| **`Empresa`** | **Sim** | `Nubank` | Nome da empresa para logs, contexto e screenshots. |
| **`Cargo`** | **Sim** | `Senior Python Engineer` | Título da vaga para contextualização de respostas. |
| **`Fit Score`** | Recomendada | `85%` ou `85` | Nota de aderência (0–100); respeita `MIN_FIT_SCORE` quando `FIT_GATE_ENABLED=true`. |
| **`Stack Principal`** | Opcional | `Python, FastAPI, Docker` | Tecnologias principais da vaga. |

### D) Caçador Diário de Vagas (`core/daily_job_hunter.py`)

Busca ativa diária em repositórios de tecnologia brasileiros (`backend-br`, `react-brasil`, `frontendbr`), Jobicy e RemoteOK com base nas *skills* cadastradas no perfil do Supabase, deduplicando links e enfileirando vagas `PENDING`:

```bash
# Executar uma varredura imediata agora:
python -m core.daily_job_hunter --run-now

# Rodar em modo agendador contínuo (disparo diário às DAILY_HUNT_HOUR, padrão 11h):
python -m core.daily_job_hunter

# Agendar tarefa automática diária no Windows Task Scheduler (11:00 AM):
powershell -ExecutionPolicy Bypass -File .\scripts\setup_daily_task_windows.ps1
```

### E) Cloud Worker Contínuo via Docker & Deploy em VPS (`cloud_worker.py`)

Para rodar como worker dedicado consumindo vagas do Supabase em Docker ou provisionar uma VPS Ubuntu 22.04/24.04 (ex.: Hetzner Cloud):

```bash
# Subida via Docker Compose:
cp .env.worker.example .env
docker compose up -d --build

# Ou provisionamento completo em VPS Ubuntu limpa (configura Swap 2GB, Docker, UFW e sobe o worker):
chmod +x deploy_vps.sh
sudo ./deploy_vps.sh
```

---

## 📊 4. Avaliação Estatística (Laya ECE) & Qualidade de Código

```bash
# Avaliar calibração estatística (ECE, cobertura e acurácia seletiva) em data/decision_goldens.json:
python scripts/eval_decisions.py

# Executar suíte completa de testes automatizados:
pytest -q

# Executar apenas testes unitários rápidos:
pytest tests/unit -q

# Lint (Ruff) e verificação estática de tipos (Mypy):
ruff check .
mypy core config
```

---

## 📚 5. Documentação de Arquitetura (Padrão Google OKF v0.2)

- 📑 **Índice Mestre do Grafo OKF**: [`docs/okf/index.md`](docs/okf/index.md)
- 🏗️ **Arquitetura & Pipeline Assíncrono**: [`docs/okf/architecture.md`](docs/okf/architecture.md)
- 🖥️ **Especificação do Painel Visual Web**: [`docs/okf/ui_specification.md`](docs/okf/ui_specification.md)
- 🎯 **Motor de Decisão Calibrada (DP1–DP6 & ECE)**: [`docs/okf/decision_engine.md`](docs/okf/decision_engine.md)
- 🛡️ **Segurança, Privacidade & Invariantes Éticos**: [`docs/okf/security_and_ethics.md`](docs/okf/security_and_ethics.md)
- 📦 **Contratos de Dados & Schemas Pydantic**: [`docs/okf/data_contract.md`](docs/okf/data_contract.md)
- 📄 **Especificação Consolidada**: [`docs/OKF_SPECIFICATION.md`](docs/OKF_SPECIFICATION.md)

---

## ⚠️ 6. Aviso Legal e Uso Ético

> [!WARNING]
> Este software foi desenvolvido para **produtividade pessoal do próprio candidato, pesquisa em sistemas multi-agentes e estudo de automação defensiva**. O usuário é integralmente responsável por:
> 1. Garantir que 100% das informações fornecidas em `data/profile.json` e nos currículos reflitam fielmente sua trajetória real;
> 2. Respeitar os Termos de Serviço (ToS) das plataformas de recrutamento e empresas destinatárias;
> 3. Supervisionar os envios realizados, utilizando o modo simulação (`--dry-run`) para validação prévia.

---

## 📄 7. Licença

Distribuído sob a **Sustainable Use License (SUL 1.0)** — modelo *Fair-Code*.  
O uso pessoal, acadêmico e interno é gratuito. É vedada a oferta comercial deste software como serviço gerenciado concorrente (SaaS) sem autorização expressa do autor. Consulte [`LICENSE`](LICENSE) para o texto integral.
