---
type: DataSpecification
title: Candidata IA — Contratos de Dados e Esquemas de Modelos
description: Esquemas Pydantic v2 de entrada e saída, estrutura da planilha central e formato do perfil do candidato.
resource: core/models.py
tags:
  - schema
  - pydantic
  - json
  - data-contract
timestamp: "2026-10-11T23:55:00Z"
---

# Contratos de Dados e Esquemas de Modelos

Esta especificação define os contratos de dados estritos implementados através de **Pydantic v2** e consumidos pelo pipeline do Candidata IA.

---

## 1. Esquema do Perfil do Candidato (`Profile`)

Armazenado em `data/profile.json` (com modelo de exemplo em `data/profile.example.json`):

```json
{
  "full_name": "Nome Completo do Candidato",
  "email": "candidato@email.com",
  "phone": "+55 11 99999-9999",
  "location": "São Paulo, SP, Brasil",
  "headline": "Engenheiro de Software Sênior",
  "links": {
    "linkedin": "https://linkedin.com/in/usuario",
    "github": "https://github.com/usuario",
    "portfolio": "https://meusite.com"
  },
  "work_authorization": "Brasileira (autorizado a trabalhar no Brasil e em regime remoto)",
  "salary_expectation": "R$ 15.000 / mês (PJ)",
  "skills": ["Python", "Playwright", "FastAPI", "Docker", "PostgreSQL"],
  "base_answers": {
    "years_python": "6 anos",
    "english_level": "Fluente / Avançado",
    "notice_period": "Imediato",
    "remote_preference": "100% Remoto"
  },
  "eeo": {
    "gender": "Masculino / Feminino / Prefer not to say",
    "race": "Decline to self-identify",
    "veteran": "Decline to self-identify",
    "disability": "Decline to self-identify"
  }
}
```

---

## 2. Contrato da Planilha de Vagas (`Job`)

> **Escopo de Atuação do Sistema**:
> O **Candidata IA** atua exclusivamente como **executor autônomo de candidaturas** (automação de preenchimento, upload de currículos, resolução de questionários e envio nos ATSs).
> 
> ⚠️ **O sistema NÃO pesquisa vagas na internet**. A prospecção e seleção de vagas deve ser realizada externamente (por um agente de busca/scraping dedicado ou pelo próprio usuário). Esse agente externo é responsável por gerar ou alimentar a planilha central (Google Sheets ou `.xlsx`/`.csv` local) que servirá de entrada para o robô.

### Requisitos Mínimos para Execução do Robô:
1. **Link da Planilha Central (`GOOGLE_SHEET_URL`)**: Planilha acessível com as colunas contratuais abaixo.
2. **Link / Nome do Currículo Otimizado**: O campo `Currículo Otimizado` deve apontar para o nome ou rótulo do PDF disponível em `cache/resumes/` ou no Google Drive (`DRIVE_FOLDER_ID`). O sistema realiza correspondência difusa (*fuzzy matching* via `difflib`) entre o valor da célula e os nomes dos PDFs.
3. **Link da Vaga**: URL direta e válida da página de candidatura da vaga (ex.: Greenhouse, Lever, Indeed, etc.).
4. **Status da Candidatura**: Para que o robô processe a linha, o valor deve ser `A Fazer` (ou em branco, se configurado `INCLUDE_BLANK_STATUS=true`).
5. **Assets Locais**: Perfil completo em `data/profile.json` e chave `LLM_API_KEY`.

### Estrutura de Colunas da Planilha

A fonte de dados de vagas (Google Sheets ou `.xlsx`/`.csv` local) deve conter as seguintes colunas obrigatórias:

| Coluna na Planilha | Tipo no Modelo | Descrição |
| :--- | :--- | :--- |
| `Data` | `str` | Data de cadastro ou identificação da vaga. |
| `Empresa` | `str` | Razão social ou nome fantasia da empresa contratante. |
| `Cargo` | `str` | Título da vaga (ex.: *Senior Backend Engineer*). |
| `Modalidade` | `str` | *Remoto*, *Híbrido* ou *Presencial*. |
| `Link da Vaga` | `str \| None` | URL direta do formulário de candidatura ou hiperlink. |
| `Currículo Otimizado` | `str \| None` | Nome do PDF ou base recomendada (ex.: *Currículo Otimizado - Audax*). |
| `Fit Score` | `int \| None` | Pontuação de aderência técnica (0 a 100). |
| `Base Utilizada` | `list[str]` | Currículos base que serviram de referência. |
| `Stack Principal` | `list[str]` | Tecnologias-chave requeridas na vaga. |
| `Status da Candidatura` | `str` | Filtro de processamento (*A Fazer*, *Concluído*, *Ignorado*). |

---

## 3. Estados de Execução (`TaskStatus`)

Enumeração canônica de estados de uma candidatura no pipeline:

```python
class TaskStatus(StrEnum):
    PENDING = "PENDING"          # Na fila aguardando processamento
    PROCESSING = "PROCESSING"    # Em execução ativa no navegador
    COMPLETED = "COMPLETED"      # Concluída com sucesso (dry-run ou submit)
    FAILED = "FAILED"            # Falha transitória após esgotar retentativas
    NEEDS_REVIEW = "NEEDS_REVIEW"# Bloqueador (CAPTCHA) ou dado obrigatório ausente
    SKIPPED = "SKIPPED"          # Rejeitada pelo filtro de fit ou ATS não suportado
```

---

## 4. Esquema de Auditoria de Decisões (`DecisionRecord`)

Gravado em `artifacts/decisions.jsonl` a cada veredicto de DP1 a DP6:

```json
{
  "dp": "DP3",
  "job_id": "sha256_hash_empresa_cargo",
  "question_id": null,
  "kind": "noul",
  "mode": "active",
  "probs": {
    "true": 0.05,
    "false": 0.95
  },
  "band": "no",
  "laya_action": "no",
  "applied_action": "no",
  "source": "laya",
  "latency_ms": 42.5,
  "state_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "ts": "2026-10-11T23:55:00Z"
}
```
> **Nota de Privacidade**: O campo `state_sha256` assegura auditabilidade matemática sem expor textos proprietários de vagas ou dados pessoais do candidato no log.

---

## 5. Resultado de Processamento (`ApplicationResult`)

Gravado em `artifacts/results.jsonl` e consumido pelo dashboard da interface:

```json
{
  "job_id": "empresa-titulo-vaga",
  "status": "COMPLETED",
  "reason": "Candidatura simulada com sucesso",
  "dry_run": true,
  "submitted": false,
  "resume_used": "Curriculo_Otimizado_Engenharia.pdf",
  "screenshot_path": "artifacts/screenshots/review_empresa_123.png",
  "attempts": 1,
  "duration_s": 14.2,
  "tokens_used": 1850,
  "prompt_tokens": 1420,
  "completion_tokens": 430,
  "started_at": "2026-10-12T17:30:00Z",
  "completed_at": "2026-10-12T17:30:14Z"
}
```

### Campos de Métricas Consolidadas:
- `duration_s` (`float`): Tempo total em segundos gasto pela IA e navegador naquela vaga específica.
- `tokens_used` (`int`): Total de tokens do LLM (Gemini/OpenAI) gastos para responder perguntas abertas e desempate.
- `prompt_tokens` e `completion_tokens` (`int`): Subdivisão dos tokens de entrada e saída.
- `resume_used` (`str`): Nome exato do arquivo PDF selecionado e anexado no ATS.
- `screenshot_path` (`Path | None`): Caminho da evidência visual capturada pelo robô para inspeção humana.

---

## 6. Contratos de Dados da Interface Web (REST & SSE)

- **`GET /api/status`**: Retorna snapshot de progresso (`is_running`, `completed_jobs`, `total_jobs`, `remaining_jobs`, `progress_percent`, `active_jobs`).
- **`GET /api/events`**: Stream Server-Sent Events (SSE) transmitindo em tempo real eventos de mudança de etapa (`step_update`), finalização de vaga (`progress_update`) e logs.
- **`GET /api/metrics`**: Agregação de KPIs globais (total de submissões reais vs simulações, tempo médio, ranking de currículos).
- **`GET /api/config` & `POST /api/config`**: Leitura e gravação sanitizada de credenciais do `.env`.
- **`GET /api/cookies` & `POST /api/cookies`**: Gerenciamento de sessão de navegador (`storage_state.json`).
