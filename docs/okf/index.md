---
type: KnowledgeBundleIndex
title: Candidata IA — Índice de Documentação Técnica
description: Índice canônico da documentação de arquitetura, motor de decisão e contratos do sistema.
tags:
  - okf
  - index
  - ia-candidata
  - open-source
  - architecture
timestamp: "2026-10-11T23:55:00Z"
---

# Candidata IA — Documentação Técnica

> **Repositório / Módulo**: `ia_candidata` (`job-application-harness`)  
> **Objetivo**: Documentar a arquitetura assíncrona, regras de decisão estatística, invariantes de segurança e contratos de dados.

---

## 1. Visão Geral

O **Candidata IA** (`job-application-harness`) é um harness assíncrono para orquestração de formulários web em plataformas ATS (**Greenhouse, Lever, Workable, Indeed** e adaptadores compatíveis). O sistema combina:
- Orquestração assíncrona em Playwright Chromium / Camoufox;
- Camada de decisão estatística e calibrada (sidecar Laya via HTTP ou fallback determinístico por regras);
- Preenchimento assistido por LLM (OpenAI-compatível) restrito estritamente aos fatos do perfil;
- Governança rigorosa de dados (invariantes de segurança e conformidade ética).

Este diretório organiza a documentação técnica em Markdown estruturado com metadados YAML frontmatter.

---

## 2. Documentos

| Documento | Tipo (`type`) | Descrição e Escopo |
| :--- | :--- | :--- |
| [Arquitetura do Sistema](architecture.md) | `SystemArchitecture` | Visão geral dos componentes, fluxo assíncrono do pipeline, execução em lote e isolamento de processos. |
| [Interface Visual e Painel Web](ui_specification.md) | `InterfaceSpecification` | Especificação da interface web local (FastAPI + SSE) para telemetria, configuração e inspeção. |
| [Sistema de Design e Identidade Visual](design_system.md) | `DesignSystemSpecification` | Tokens semânticos, paleta cromática, tipografia tabular e inventário de assets. |
| [Motor de Decisão Calibrada](decision_engine.md) | `DecisionEngine` | Especificação formal dos pontos de decisão DP1 a DP6, sidecar Laya, medição de ECE e limiares de incerteza. |
| [Segurança, Ética e Invariantes](security_and_ethics.md) | `SecurityPolicy` | Regras invioláveis de proteção de dados sensíveis (EEO), anti-alucinação, isolamento de PII e tratamento de ToS. |
| [Contratos de Dados e Esquemas](data_contract.md) | `DataSpecification` | Modelos Pydantic v2 (`Job`, `Profile`, `DecisionRecord`, `ApplicationResult`) e esquema de planilhas. |
| [Prontidão para Open Source](open_source_readiness.md) | `OpenSourceStrategy` | Diagnóstico de segurança, plano de sanitização de credenciais, escolha de licença e governança de comunidade. |
| [Histórico de Mudanças e Decisões](log.md) | `Changelog` | Registro cronológico reservado de evolução do conhecimento e decisões de arquitetura (ADRs). |

---

## 3. Guia Rápido de Leitura

Ordem recomendada para revisão técnica do sistema:
1. **Fluxo assíncrono e módulos principais**: Ler [architecture.md](architecture.md).
2. **Painel local FastAPI + SSE**: Ler [ui_specification.md](ui_specification.md).
3. **Design tokens e CSS**: Ler [design_system.md](design_system.md).
4. **Motor de decisão híbrido (DP1–DP6 & ECE)**: Ler [decision_engine.md](decision_engine.md).
5. **Invariantes de segurança e higiene**: Ler [security_and_ethics.md](security_and_ethics.md) e [open_source_readiness.md](open_source_readiness.md).
6. **Contratos de dados Pydantic**: Consultar [data_contract.md](data_contract.md).
