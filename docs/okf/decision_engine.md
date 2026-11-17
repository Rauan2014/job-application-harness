---
type: DecisionEngine
title: Candidata IA — Motor de Decisão Calibrada e Sidecar Laya
description: Especificação matemática, protocolos HTTP, calibração estatística (ECE) e pontos de decisão DP1 a DP6.
resource: core/decision/layer.py
tags:
  - machine-learning
  - calibration
  - ece
  - laya-mcp
  - decision-theory
timestamp: "2026-10-11T23:55:00Z"
---

# Motor de Decisão Calibrada e Sidecar Laya

A tomada de decisão no **Candidata IA** não delega escolhas críticas a prompts genéricos de LLM. Ela é fundamentada em uma **camada de decisão calibrada** com teoria de probabilidades, medição de incerteza e fallback determinístico.

---

## 1. Topologia de Decisão e Protocolo Laya

O motor opera em conjunto com o sidecar **Laya-MCP** (`http://127.0.0.1:8787`):
- **Isolamento de Processo**: O modelo de ML roda em processo separado; falhas no sidecar nunca travam o loop assíncrono do harness.
- **Circuit Breaker**: Falhas consecutivas (`LAYA_MAX_FAILURES=3`) acionam o disjuntor, degradando as decisões silenciosamente para o motor de regras.
- **Mitigação de Viés de Posição (`Order-Swap`)**: Em perguntas de múltipla escolha (choice), o cliente executa chamadas permutadas. Se houver divergência entre as ordens (`order_consistent=False`), a margem cai a 0 e o resultado é marcado como incerto.

---

## 2. Os 6 Pontos de Decisão Tipados (DP1 a DP6)

| Ponto de Decisão | Tipo de Pergunta | Objetivo do Julgamento | Comportamento de Fallback |
| :--- | :--- | :--- | :--- |
| **DP1 (`job_fit`)** | `noul` (Sim/Não) | Avalia se a descrição da vaga é compatível com o perfil profissional antes de gastar recursos de browser/LLM. | Nota mínima de fit da planilha (`MIN_FIT_SCORE`, padrão 70). |
| **DP2 (`resume_fallback`)** | `choice` | Seleciona o melhor currículo base quando a planilha não especifica uma versão otimizada. | Heurística por idioma (PT vs EN) + LLM com retry estruturado. |
| **DP3 (`blocker_check`)** | `noul` (Sim/Não) | Identifica CAPTCHA, Cloudflare Turnstile, hCaptcha ou login obrigatório na tela do formulário. | Expressões regulares em `_BLOCKER_PATTERNS` em texto visível e iframes. |
| **DP4 (`question_sensitivity`)** | `noul` (Sim/Não) | Detecta se uma pergunta do formulário refere-se a dados protegidos/EEO (gênero, raça, orientação sexual, deficiência). | Regex determinístico em `_SENSITIVE_PATTERNS`. Resposta vem exclusivamente de `profile.eeo`. |
| **DP5 (`field_mapping`)** | `choice` | Mapeia labels variados do formulário para campos canônicos do perfil (`headline`, `linkedin`, `salary`). | Dicionário de sinônimos determinísticos. Pula chamada LLM. |
| **DP6 (`ready_to_submit`)** | `noul` (Sim/Não) | Checagem final: todos os campos obrigatórios estão preenchidos? Nenhum blocker ativo? Dry-run verificado? | Validação estrita de árvore DOM e campos `required`. |

---

## 3. Modos Operacionais e Invariante de Segurança

Cada ponto de decisão pode ser configurado em três modos:
1. **`off`**: Desativado; utiliza exclusivamente regras determinísticas.
2. **`shadow`**: Executa o Laya em paralelo e audita o veredicto em `artifacts/decisions.jsonl`, mas aplica a decisão da regra determinística.
3. **`active`**: O veredicto do Laya é soberano para a tomada de ação, respeitadas as invariantes.

### Invariante de Assimetria de Segurança
> [!IMPORTANT]
> O modelo probabilístico (Laya) **NUNCA** pode revogar um bloqueio de segurança imposto pelas regras determinísticas em DP3 (Blocker), DP4 (Sensibilidade) e DP6 (Ready to Submit). Se a regra acusar "Bloqueado" ou "Sensível", essa decisão é imutável.

---

## 4. Calibração Estatística e Rebaixamento Automático

Decisões probabilísticas só podem ser ativadas (`active`) se o modelo demonstrar confiabilidade estatística comprovada por benchmark:
- **Expected Calibration Error (ECE)**: Mede o descompasso entre as probabilidades preditas e a acurácia real observada.
- **Dataset Golden**: Avaliado contra `data/decision_goldens.json` contendo exemplos rotulados sintéticos e reais.
- **Validade Temporal**: O relatório de calibração (`artifacts/laya_eval.json`) expira em 14 dias (`LAYA_EVAL_MAX_AGE_DAYS`). Relatórios vencidos rebaixam automaticamente os DPs ativos para `shadow`.

```
               Probabilidade Laya (p_true)
0.0 ---------------- 0.2 ------------ 0.8 ---------------- 1.0
 [    Banda NO    ]     [   INCERTO   ]     [    Banda YES   ]
 (Ação Rejeitada)        (Fallback)          (Ação Aprovada)
```

- Limiar de rejeição: $p \le 0.20$
- Limiar de aceitação: $p \ge 0.80$
- Zona cinzenta ($0.20 < p < 0.80$): Veredicto `uncertain` $\rightarrow$ aciona fallback determinístico sem risco de falso positivo.

---

Consulte [security_and_ethics.md](security_and_ethics.md) para compreender as restrições éticas sobre dados e formulários.
