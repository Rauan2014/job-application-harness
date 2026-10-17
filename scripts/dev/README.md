# Scripts de Desenvolvimento / Depuração

Utilitários de teste manual e inspeção rápida para desenvolvimento:

- `inspect_lever.py`: Inspeciona campos e perguntas obrigatórias diretamente em uma URL de vaga da Lever (aceita URL opcional via argumento CLI).
- `test_gemini.py`: Testa chamada direta à LLM configurada via `Matcher.answer_questions`.
- `test_single_job.py`: Executa o pipeline completo de ponta a ponta para uma única vaga específica.
- `test_captcha_vis.py`: Testa detecção visual e heurística de verificação antibot/CAPTCHA.

## Scripts Arquivados (`archive/`)

Scripts de depuração pontual com URLs hardcoded foram arquivados na pasta `archive/`. Os cenários relevantes de teste (Ashby, Quickin, Sólides, Kognit e Jump) foram consolidados como testes automatizados parametrizados em `tests/unit/test_aggregator_handler.py`.
