<!--
README: Respostas simuladas do sidecar laya-mcp baseadas no formato assumido (laya_mcp 0.2.2).
Substituir por respostas capturadas de um sidecar real executando:
laya-mcp serve --model multilingual --port 8787
-->
# Fixtures Laya

Arquivos JSON com envelopes reais do sidecar:
- `health.json`: status 200 normal com device GPU/CUDA.
- `health_degraded.json`: status 200 com demoted_to_cpu / degraded: true.
- `plan_fits.json`: resposta de /plan sem truncamento.
- `plan_truncated.json`: resposta de /plan indicando truncamento de estado.
- `ask_noul.json`: resposta de /ask para pergunta noul.
- `ask_choice_1.json`: resposta de /ask para múltipla escolha (1ª ordem).
- `ask_choice_2.json`: resposta de /ask para múltipla escolha (2ª ordem).
