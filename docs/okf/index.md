---
type: KnowledgeBundleIndex
title: Candidata IA — Índice Mestre de Conhecimento (OKF)
description: Índice canônico e grafo de navegação do sistema Candidata IA segundo a especificação Open Knowledge Format (Google Cloud).
tags:
  - okf
  - index
  - ia-candidata
  - open-source
  - architecture
timestamp: "2026-10-11T23:55:00Z"
---

# Candidata IA — Grafo de Conhecimento do Sistema (OKF)

> **Padrão de Especificação**: Open Knowledge Format (Google Cloud, v0.2)  
> **Repositório / Módulo**: `ia_candidata` (`job-harness`)  
> **Objetivo**: Documentar integralmente a arquitetura, regras de decisão, políticas éticas/segurança e prontidão de abertura open source para consumo simultâneo por humanos e agentes de IA.

---

## 1. Visão Geral do Grafo de Conhecimento

O **Candidata IA** é um sistema autônomo e defensivo de automação de candidaturas em plataformas de vagas (Applicant Tracking Systems - ATS, como Lever e Greenhouse e agregadores como Indeed). O sistema combina:
- Orquestração assíncrona em Playwright (Chromium);
- Camada de decisão estatística e calibrada (sidecar Laya via HTTP ou fallback determinístico por regras);
- Preenchimento ético assistido por LLM (OpenAI-compatível) restrito estritamente aos fatos do perfil;
- Governança rigorosa de dados (invariantes de segurança e conformidade ética).

Este diretório implementa o padrão **Open Knowledge Format (OKF)** do Google Cloud, estruturado em Markdown com metadados YAML frontmatter e interligações em grafo.

---

## 2. Nós do Conhecimento (Navegação do Grafo)

| Documento | Tipo (`type`) | Descrição e Escopo |
| :--- | :--- | :--- |
| [Arquitetura do Sistema](architecture.md) | `SystemArchitecture` | Visão geral dos componentes, fluxo assíncrono do pipeline, execução em lote e isolamento de processos. |
| [Interface Visual e Painel Web](ui_specification.md) | `InterfaceSpecification` | Especificação completa da interface gráfica para operação, configuração de API/cookies e acompanhamento em tempo real sem uso de terminal. |
| [Sistema de Design e Identidade Visual](design_system.md) | `DesignSystemSpecification` | Especificação canônica da identidade visual única "Studio Edition", tokens semânticos, paleta cromática e inventário de assets. |
| [Motor de Decisão Calibrada](decision_engine.md) | `DecisionEngine` | Especificação formal dos pontos de decisão DP1 a DP6, sidecar Laya, medição de ECE e limiares de incerteza. |
| [Segurança, Ética e Invariantes](security_and_ethics.md) | `SecurityPolicy` | Regras invioláveis de proteção de dados sensíveis (EEO), anti-alucinação, isolamento de PII e tratamento de ToS. |
| [Contratos de Dados e Esquemas](data_contract.md) | `DataSpecification` | Modelos Pydantic v2 (`Job`, `Profile`, `DecisionRecord`, `ApplicationResult`) e esquema de planilhas. |
| [Prontidão para Open Source](open_source_readiness.md) | `OpenSourceStrategy` | Diagnóstico de segurança, plano de sanitização de credenciais, escolha de licença e governança de comunidade. |
| [Histórico de Mudanças e Decisões](log.md) | `Changelog` | Registro cronológico reservado de evolução do conhecimento e decisões de arquitetura (ADRs). |

---

## 3. Guia de Travessia para Agentes de IA

Ao processar este repositório, agentes cognitivos e modelos de linguagem devem adotar a seguinte hierarquia de consulta:
1. **Dúvidas sobre fluxo de execução e arquivos**: Ler [architecture.md](architecture.md).
2. **Dúvidas sobre a interface web e painel do usuário**: Ler [ui_specification.md](ui_specification.md).
3. **Dúvidas sobre identidade visual, tokens e CSS**: Ler [design_system.md](design_system.md).
4. **Dúvidas sobre heurísticas de triagem, Laya e regras**: Ler [decision_engine.md](decision_engine.md).
5. **Auditoria de segurança, segredos e conformidade**: Ler [security_and_ethics.md](security_and_ethics.md) e [open_source_readiness.md](open_source_readiness.md).
6. **Validação de tipos e campos esperados**: Consultar [data_contract.md](data_contract.md).
