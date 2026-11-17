# Scripts de Desenvolvimento / Depuração

Utilitários manuais para inspeção de formulários e validação isolada de componentes:

- `inspect_lever.py`: Inspeciona campos e perguntas obrigatórias em uma URL de formulário Lever (recebe URL via argumento CLI).
- `test_gemini.py`: Valida integração estruturada de `Matcher.answer_questions` com o provedor LLM configurado.
- `test_single_job.py`: Executa o pipeline em dry-run para uma vaga única de exemplo (ou URL passada via argumento CLI).
- `test_captcha_vis.py`: Inspeciona iframes de verificação antibot/CAPTCHA em uma URL passada via CLI.
