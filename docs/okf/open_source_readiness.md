---
type: OpenSourceStrategy
title: Job Application Harness — Governança, Segurança e Higiene de Repositório
description: Controles de segurança aplicados, higienização de credenciais, escolha de licença e diretrizes de governança.
resource: README.md
tags:
  - open-source
  - security-audit
  - license
  - sanitization
  - governance
timestamp: "2026-10-11T23:55:00Z"
---

# Governança, Segurança e Prontidão Open Source

Este documento resume os controles de segurança, higiene de repositório e licenciamento adotados no **Candidata IA (`ia_candidata`)**.

---

## 1. Diferenciais de Engenharia

Soluções ingênuas de automação web costumam falhar por timeouts cegos, ausência de testes automatizados e falta de limites éticos. A arquitetura deste repositório prioriza:
1. **Execução assíncrona resiliente** com `asyncio.Semaphore`, esperas reativas no DOM e idempotência pós-envio (`PostSubmitError`);
2. **Camada de decisão híbrida e calibrada** (`DP1`–`DP6`) com validação estatística (ECE / selective accuracy) e fallback determinístico;
3. **Suíte de testes determinística offline** (mocks HTTP com `respx` + servidores ATS simulados via `aiohttp` + Playwright).

---

## 2. Controles de Higiene e Segurança Implementados

- **Templates sem segredos**: `.env.example`, `.env.worker.example` e `data/profile.example.json` utilizam exclusivamente valores fictícios/placeholders.
- **Isolamento via `.gitignore`**: `.env`, `client_secret*.json`, `token*.json`, `storage_state*.json`, `data/profile.json`, `cache/` e `artifacts/` são bloqueados no controle de versão.
- **Blindagem de dados sensíveis e logs**: Perguntas EEO nunca são enviadas a APIs externas e logs de auditoria (`artifacts/decisions.jsonl`) registram apenas `state_sha256`.
- **Fallback dinâmico nos handlers**: Campos de perfil leem estritamente os valores configurados no `Profile`, sem URLs ou identificadores pessoais fixos no código.
- **Segurança na camada Web**: Mascaramento de `LLM_API_KEY` em `/api/config`, validação contra *path traversal* e CORS restrito a `localhost` / `127.0.0.1`.

---

## 3. Estratégia de Licenciamento

O projeto adota o modelo **Fair-Code** por meio da **Sustainable Use License (SUL 1.0)** (arquivo [`LICENSE`](../../LICENSE)):
- Uso pessoal, acadêmico e interno gratuito;
- Vedação à redistribuição comercial como serviço gerenciado (SaaS) concorrente sem autorização expressa do autor.
