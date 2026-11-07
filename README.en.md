# Job Application Harness

<p align="left">
  🇺🇸 <strong>English</strong> &nbsp;|&nbsp; 🇧🇷 <a href="README.md"><strong>Ler em Português (README.md)</strong></a>
</p>

An open-source (*Fair-Code* — SUL 1.0) asynchronous harness for **ethical, calibrated, and auditable job application automation** across major Applicant Tracking Systems (**Greenhouse, Lever, Workable, Indeed**, and compatible job aggregators). It combines anti-detect browser orchestration (**Camoufox / Playwright Chromium**), calibrated statistical decision gates (**Laya DP1–DP6**), automated daily job discovery, fuzzy PDF resume resolution, and LLM-assisted form filling strictly grounded in the candidate's verified profile.

---

## ☁️ Candidata Cloud (Managed Zero-Setup Cloud Platform)

> [!TIP]
> **Prefer a zero-install experience without configuring Python, browser binaries, API keys, or local JSON files?**
>
> Check out **[Candidata Cloud](https://candidata.cloud)** — the official managed cloud platform built on top of this engine:
> - 📄 **AI Resume Import**: Drag and drop your PDF resume to build a structured ATS profile in seconds.
> - 🤖 **Managed Cloud Execution & Visual Proof**: Full-page verification screenshots for every application in Dry-Run or Live Submission mode.
> - 🛡️ **Anti-Hallucination Guardrail & 1-Click HITL**: Automatically pauses whenever an unfamiliar required recruiter question needs your confirmation.

---

## ✨ Key Features

- **Asynchronous Multi-ATS Engine**: Dedicated ATS handlers in `automation/handlers/` for **Greenhouse**, **Lever**, **Workable**, **Indeed**, and **Aggregators** (`automation/registry.py`), with bounded concurrency (`MAX_CONCURRENCY`), exponential backoff, and analytics tracker blocking.
- **Stealth & Standard Browser Engines**: Native support for **Camoufox** (anti-fingerprinting, enabled by default) and **Playwright Chromium** (`--browser {camoufox,chromium}`), with persistent cookie/session reuse via `cache/storage_state.json`.
- **4 Integrated Job Ingestion Modes**:
  1. **On-Demand Live Job Search (`--search`)**: Real-time job discovery and immediate queue ingestion (`core/job_search.py`).
  2. **Automated Daily Job Hunter (`core/daily_job_hunter.py`)**: Active daily web scraping across GitHub Tech BR, Jobicy, and RemoteOK matched against candidate skills, with automatic URL deduplication and `PENDING` queue insertion.
  3. **Google Sheets / Local `.xlsx` & `.csv` (`--sheet`)**: Direct ingestion (including public read-only links with zero Google Cloud setup) plus automatic status writeback locally and online.
  4. **Supabase Cloud Queue (`--from-supabase` / `cloud_worker.py`)**: Concurrency-safe job claiming via PostgreSQL RPC `worker_claim_next_job` (`FOR UPDATE SKIP LOCKED`).
- **Calibrated Decision Layer (DP1–DP6)**: Supports the local **Laya** neural sidecar (gated by Expected Calibration Error / ECE evaluation) with automatic fallback to deterministic `rules`, plus a **100% local/offline mode (`--no-llm`)** with zero external LLM calls.
- **Safety & Ethical Guardrails by Default**:
  - `DRY_RUN=true` by default (fills forms completely and captures verification screenshots without clicking final submit unless `--no-dry-run` is explicitly passed).
  - Confidential **EEO / GDPR / LGPD Shield** for voluntary self-identification fields.
  - Configurable autonomous mode (`--autonomous`) or strict Human-in-the-Loop pause (`--no-autonomous` → `NEEDS_REVIEW`).
- **Local Visual Control Panel (`web/`)**: Built-in FastAPI + Server-Sent Events (SSE) web UI at `http://127.0.0.1:8000` to edit 100% of `.env` variables, manage candidate profile & cookies, and inspect live screenshots and telemetry.

---

## 🏗️ Repository Structure

```text
job-application-harness/
├── main.py                  # Primary async batch CLI (spreadsheets, live search, or Supabase queue)
├── cloud_worker.py          # Continuous production worker daemon backed by Supabase RPC queue
├── deploy_vps.sh            # Automated Ubuntu VPS provisioning (2GB Swap, Docker, UFW & Worker)
├── automation/              # BrowserManager (Camoufox/Chromium) & ATS handlers (Greenhouse, Lever, Workable, Indeed...)
├── core/                    # Pipeline, LLM Matcher, live job search, daily_job_hunter & decision engine (Laya/Rules)
├── config/                  # Typed configuration via Pydantic Settings (.env)
├── storage/                 # Google Sheets, Google Drive, and Supabase Cloud clients
├── web/                     # Local visual dashboard (FastAPI + SSE + full configuration & cookie manager)
├── scripts/                 # Google OAuth helper, login_browser, sync_sheet_resumes, Laya ECE eval & Task Scheduler
├── data/                    # Example candidate profile (profile.example.json) & golden evaluation set
└── docs/okf/                # Architecture knowledge graph following Google Open Knowledge Format (OKF v0.2)
```

---

## 🚀 1. Installation

### Prerequisites
- **Python 3.11+**
- **Git**

### Step-by-Step Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Rauan2014/job-application-harness.git
   cd job-application-harness
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   # Windows (PowerShell):
   .\.venv\Scripts\Activate.ps1
   # Linux / macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt

   # Optional — testing, linting, and static type checking:
   pip install -r requirements-dev.txt

   # Optional — Laya neural decision sidecar:
   pip install -r requirements-laya.txt
   ```

4. **Install browser binaries:**
   ```bash
   playwright install chromium
   # Fetch Camoufox anti-detect browser (default browser engine):
   python -m camoufox fetch
   ```

---

## ⚙️ 2. Configuration

1. **Environment Variables (`.env`):**
   ```bash
   # Windows:
   copy .env.example .env
   # Linux / macOS:
   cp .env.example .env
   ```
   Key settings in `.env`:
   - `LLM_API_KEY`: API key for any OpenAI-compatible provider (Google AI Studio / Gemini, OpenAI, OpenRouter, Groq).
   - `LLM_BASE_URL`: Provider base URL (e.g., `https://generativelanguage.googleapis.com/v1beta/openai/`).
   - `LLM_MODEL`: Model identifier (e.g., `gemini-2.5-flash` or `gpt-4o-mini`).
   - `DRIVE_FOLDER_ID`: Google Drive folder ID for resumes. **Tip:** if you store PDFs locally inside `cache/resumes/` without Google Drive, set `DRIVE_FOLDER_ID=none`.
   - `GOOGLE_SHEET_URL`: Full Google Sheets URL or spreadsheet ID (sheets shared as *"Anyone with the link can view"* work out-of-the-box without Google Cloud credentials).
   - `DRY_RUN`: Defaults to `true` (safe simulation mode).

2. **Candidate Profile (`data/profile.json`):**
   ```bash
   # Windows:
   copy data\profile.example.json data\profile.json
   # Linux / macOS:
   cp data/profile.example.json data/profile.json
   ```
   Populate your real contact details, portfolio/LinkedIn/GitHub links, work authorization, salary expectations, base answers, and EEO preferences, and place your `.pdf` resumes inside `cache/resumes/`.

3. **Optional Session & Google Sync Utilities:**
   ```bash
   # Log in interactively once (e.g., Indeed, LinkedIn, Google) and persist session cookies to cache/storage_state.json:
   python scripts/login_browser.py --url https://br.indeed.com

   # Google Drive / Sheets OAuth (only needed for private Drive folders or private Sheet writeback):
   python scripts/auth_google.py

   # Export Google Docs resumes linked via HYPERLINK formulas in your spreadsheet into cache/resumes/:
   python scripts/sync_sheet_resumes.py
   ```

---

## 🖥️ 3. Usage

### A) Local Visual Dashboard (Recommended for Desktop Use)

Launch the local web dashboard and SSE telemetry inspector at `http://127.0.0.1:8000`:

```bash
python -m web
# Or run iniciar_painel.bat (Windows) / ./iniciar_painel.sh (Linux/macOS)
```

Dashboard capabilities:
- Real-time SSE event stream (`/api/events`) across `inspect`, `fill`, `submit`, and DP1–DP6 decision gates;
- Visual editor for 100% of `.env` variables, candidate profile fields, and browser session cookies;
- Screenshot evidence viewer (`/api/screenshots`) and token/latency metrics (`/api/metrics`).

### B) Command-Line Interface (`main.py`)

```bash
# 1. Safe dry-run simulation (default — fills forms and saves screenshots without submitting):
python main.py --limit 3

# 2. Live native job search + immediate dry-run execution:
python main.py --search "Senior Python Engineer" --limit 5

# 3. Run from a local spreadsheet (.xlsx/.csv) or Google Sheets URL:
python main.py --sheet data/vagas.xlsx --limit 5

# 4. Run 100% offline/deterministic using rules only (zero external LLM calls):
python main.py --limit 3 --backend rules --no-llm

# 5. Enforce strict Human-in-the-Loop pause whenever required fields are missing:
python main.py --limit 3 --no-autonomous

# 6. Live submission mode (disables dry-run and submits applications on the ATS):
python main.py --limit 3 --no-dry-run

# 7. Process pending jobs directly from Supabase:
python main.py --from-supabase --limit 10
```

#### CLI Flags Reference (`python main.py --help`)

| Flag | Description | Default |
| :--- | :--- | :--- |
| `--sheet PATH_OR_URL` | Local `.xlsx`/`.csv` path or Google Sheets URL/ID | `GOOGLE_SHEET_URL` or `data/vagas.xlsx` |
| `--search QUERY` | Runs live job search for the query and queues matching jobs | Disabled |
| `--from-supabase` | Loads and syncs pending jobs directly from Supabase (`job_applications`) | `False` |
| `--profile PATH` | Path to the candidate profile JSON file | `data/profile.json` |
| `--limit N` | Maximum number of jobs to process in this run | Unlimited |
| `--dry-run` / `--no-dry-run` | Safe simulation mode (default) vs. live ATS submission | `--dry-run` (`True`) |
| `--concurrency N` | Maximum concurrent browser sessions | `MAX_CONCURRENCY` (`3`) |
| `--backend {laya,rules}` | Decision engine: `laya` neural sidecar or deterministic `rules` | `DECISION_BACKEND` (`laya`) |
| `--browser {camoufox,chromium}` | Browser engine: `camoufox` (anti-detect) or `chromium` | `USE_CAMOUFOX` (`camoufox`) |
| `--no-llm` | Disables external LLM calls, operating 100% with local rules + profile | `False` |
| `--autonomous` / `--no-autonomous` | Synthesize missing required answers vs. pause for human review | `--autonomous` (`True` on CLI) |
| `--sync-drive` | Sync PDF resumes from Google Drive before starting | `False` |
| `--include-blank-status` / `--no-include-blank-status` | Include or skip spreadsheet rows with an empty status cell | `INCLUDE_BLANK_STATUS` (`True`) |
| `--include-review-status` | Include jobs marked as `Revisar` (`Needs Review`) | `False` |
| `--include-failed` | Retry jobs marked as `Falhou` (`Failed`) | `False` |
| `--retry-all` (`--all`) | Process all non-completed rows (`A Fazer`, `Revisar`, `Falhou`, and blank) | `False` |

### C) Spreadsheet Schema (When using `--sheet` / Google Sheets)

| Column Header (PT-BR) | Required? | Example | Description |
| :--- | :---: | :--- | :--- |
| **`Link da Vaga`** (or `URL da Vaga`) | **Yes** | `https://jobs.lever.co/company/abc123` | Direct URL to the ATS application page. |
| **`Currículo Otimizado`** | **Yes** | `Base English` | PDF filename or label matched via fuzzy search in `cache/resumes/`. |
| **`Status da Candidatura`** | **Yes** | `A Fazer` | Filtered on load and updated upon completion (`Concluído`, `Revisar`, `Falhou`). |
| **`Empresa`** | **Yes** | `Stripe` | Company name used for context, logs, and screenshot naming. |
| **`Cargo`** | **Yes** | `Senior Backend Engineer` | Role title used for contextual answer generation. |
| **`Fit Score`** | Recommended | `85%` or `85` | Technical match score (0–100); enforced when `FIT_GATE_ENABLED=true`. |
| **`Stack Principal`** | Optional | `Python, FastAPI, Docker` | Primary tech stack for the role. |

### D) Automated Daily Job Hunter (`core/daily_job_hunter.py`)

Runs active job discovery across Brazilian tech boards (`backend-br`, `react-brasil`, `frontendbr`), Jobicy, and RemoteOK matched against `candidate_profiles.skills` in Supabase, deduplicating URLs and inserting `PENDING` jobs automatically:

```bash
# Run an immediate job hunt cycle now:
python -m core.daily_job_hunter --run-now

# Run as a continuous daily scheduler (fires daily at DAILY_HUNT_HOUR, default 11:00):
python -m core.daily_job_hunter

# Register a daily Windows Scheduled Task (11:00 AM):
powershell -ExecutionPolicy Bypass -File .\scripts\setup_daily_task_windows.ps1
```

### E) Continuous Cloud Worker & Automated VPS Deployment (`cloud_worker.py`)

To run as a containerized background worker polling jobs from Supabase via Docker Compose, or provision a fresh Ubuntu 22.04/24.04 VPS (e.g., Hetzner Cloud):

```bash
# Run with Docker Compose:
cp .env.worker.example .env
docker compose up -d --build

# Or provision a fresh Ubuntu VPS end-to-end (configures 2GB Swap, Docker, UFW firewall & starts worker):
chmod +x deploy_vps.sh
sudo ./deploy_vps.sh
```

---

## 📊 4. Statistical Evaluation (Laya ECE) & Code Quality

```bash
# Evaluate Laya calibration (ECE, selective accuracy, and coverage) on data/decision_goldens.json:
python scripts/eval_decisions.py

# Run the full test suite:
pytest -q

# Run fast unit tests only:
pytest tests/unit -q

# Lint (Ruff) and static type check (Mypy):
ruff check .
mypy core config
```

---

## 📚 5. Architecture Knowledge Graph (Google OKF v0.2)

- 📑 **Master OKF Index**: [`docs/okf/index.md`](docs/okf/index.md)
- 🏗️ **System Architecture & Async Pipeline**: [`docs/okf/architecture.md`](docs/okf/architecture.md)
- 🖥️ **Web Dashboard UI Specification**: [`docs/okf/ui_specification.md`](docs/okf/ui_specification.md)
- 🎯 **Calibrated Decision Engine (DP1–DP6 & ECE)**: [`docs/okf/decision_engine.md`](docs/okf/decision_engine.md)
- 🛡️ **Security, Privacy & Ethical Invariants**: [`docs/okf/security_and_ethics.md`](docs/okf/security_and_ethics.md)
- 📦 **Data Contracts & Pydantic Schemas**: [`docs/okf/data_contract.md`](docs/okf/data_contract.md)
- 📄 **Consolidated Technical Specification**: [`docs/OKF_SPECIFICATION.md`](docs/OKF_SPECIFICATION.md)

---

## ⚠️ 6. Legal Disclaimer & Ethical Use

> [!WARNING]
> This software is built strictly for **personal candidate productivity, multi-agent systems research, and defensive browser automation study**. By using this software, you agree that you are solely responsible for:
> 1. Ensuring 100% of the data in `data/profile.json` and your resumes accurately represent your real qualifications and work history;
> 2. Complying with the Terms of Service (ToS) of recruitment platforms and hiring organizations;
> 3. Auditing automated runs and validating behavior in simulation mode (`--dry-run`) prior to live submissions.

---

## 📄 7. License

Distributed under the **Sustainable Use License (SUL 1.0)** — *Fair-Code*.  
Personal, academic, and internal corporate use are free of charge. Offering this software commercially as a competing managed service (SaaS) without explicit permission from the author is prohibited. See [`LICENSE`](LICENSE) for full details.
