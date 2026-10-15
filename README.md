# Candidata IA — Automação Ética de Candidaturas

Sistema autônomo de automação de candidaturas em plataformas de vagas (Lever, Greenhouse e Agregadores compatíveis) com triagem por aprendizado de máquina calibrado (Laya), resolução de currículos e preenchimento ético assistido por LLM.

---

## 🖥️ Painel Visual (Interface Web para Usuários Não-Técnicos)

Para usuários que preferem **não utilizar terminais, IDEs ou comandos de código**, o Candidata IA conta com uma interface gráfica completa e reativa:

```
iniciar_painel.bat   (Duplo clique no Windows para abrir automaticamente)
# Ou via linha de comando:
python -m web
```

### O que você pode fazer pelo Painel Visual:
- 📊 **Acompanhamento ao Vivo (HUD)**: Veja exatamente em qual vaga e etapa o navegador está a cada segundo (*"Inspecionando formulário"*, *"Gerando respostas com IA"*, *"Preenchendo campos"*), com barra de progresso em tempo real e contador de vagas restantes.
- 📄 **Currículos Enviados**: Lista detalhada mostrando o nome exato do PDF selecionado e anexado para cada empresa.
- ⏱️ **Tempo da IA**: Duração medida em segundos por vaga e tempo total acumulado da sessão.
- 🪙 **Tokens Gastos**: Quantidade precisa de tokens de inteligência artificial consumidos por candidatura (prompt vs completion).
- ⚠️ **Revisão Humana & Screenshots**: Visualizador integrado de evidências que permite abrir e ampliar a captura de tela da página onde a IA pausou por CAPTCHA, blocker ou dúvida.
- 🔑 **Configuração de Chaves de API**: Insira suas chaves do Google AI Studio (Gemini) ou OpenAI com mascaramento de segurança e teste a conexão em tempo real com um clique.
- 🍪 **Gerenciador de Cookies de Contas**: Faça login visual no Indeed/Gupy/LinkedIn com um clique ou cole cookies de extensões de navegador (*EditThisCookie*) para evitar telas de login e bloqueios.
- 📚 **Documentação Completa**: Disponível na pasta [OKF/](OKF/) seguindo o padrão Open Knowledge Format.

---

## 🎯 Como Funciona o Sistema & Pré-Requisitos de Dados

> **Escopo da Ferramenta**: O **Candidata IA** é um **executor de candidaturas**, projetado para automatizar o processo de preenchimento, upload de currículos, resolução de perguntas e envio nos ATSs (*Greenhouse, Lever, Indeed, Workable, RecrutaSimples, etc.*).
> 
> ⚠️ **O sistema NÃO pesquisa vagas na internet**. A prospecção e seleção de vagas deve ser feita previamente por você ou por um agente de busca/scraping externo, que deve popular a **Planilha Central de Vagas**.

### 📋 Estrutura Necessária na Planilha (Google Sheets / Excel)
Para que a automação rode, o agente de pesquisa ou usuário precisa fornecer uma planilha contendo as seguintes colunas essenciais:

| Coluna | Obrigatória? | Exemplo | Descrição |
| :--- | :---: | :--- | :--- |
| **`Link da Vaga`** (ou `URL da Vaga`) | **Sim** | `https://jobs.lever.co/empresa/abc123` | URL direta para a página de candidatura da vaga. |
| **`Currículo Otimizado`** | **Sim** | `Currículo Otimizado - Empresa X` ou `Base Inglês` | Nome do arquivo PDF ou rótulo do currículo a ser anexado. O sistema usa busca aproximada (*fuzzy matching*) na pasta de currículos. |
| **`Status da Candidatura`** | **Sim** | `A Fazer` | Define quais vagas processar. O robô filtra por `A Fazer` (ou em branco, se `INCLUDE_BLANK_STATUS=true`). Ao terminar, atualiza para `Concluído` ou `Revisar`. |
| **`Empresa`** | **Sim** | `Nubank`, `Sinqia` | Identificador da empresa para registro, screenshots e matching. |
| **`Cargo`** | **Sim** | `Senior Python Engineer` | Título da vaga para contextualizar respostas da IA. |
| **`Fit Score`** | Recomendada | `85%` ou `85` | Score de 0 a 100 de aderência técnica. Se `FIT_GATE_ENABLED=true`, vagas abaixo da nota de corte (ex: 70) são ignoradas. |
| **`Stack Principal`** | Opcional | `Python, FastAPI, Docker` | Tecnologias exigidas, usadas para apoiar a formulação de respostas. |
| **`Base Utilizada`** | Opcional | `Cognition / Base Português` | Indica qual versão base gerou o currículo otimizado. |
| **`Data`** | Opcional | `15/10/2026` | Data de inclusão da vaga. |

### 📦 O que mais é necessário para o robô rodar:
1. **Link da Planilha (`GOOGLE_SHEET_URL`)**: Informado no `.env` ou colado na aba *Configurações* do painel visual. Se compartilhada como *"Qualquer pessoa com o link pode ler"*, dispensa credenciais de nuvem.
2. **Currículos em PDF**: Arquivos `.pdf` salvos na pasta local [`cache/resumes/`](cache/resumes) ou no Google Drive (`DRIVE_FOLDER_ID`).
3. **Perfil do Candidato (`data/profile.json`)**: Arquivo com os dados reais do candidato (contato, links de LinkedIn/GitHub, pretensão salarial e respostas de autodeclaração).
4. **Chave de IA (`LLM_API_KEY`)**: Chave gratuita do Google AI Studio (Gemini) ou OpenAI para interpretação e preenchimento contextual.

---

## 1. Requisitos e Instalação

### Pré-requisitos
- Python 3.11+
- Git (opcional, para controle de versão local)

### Instalação passo a passo
1. Clone ou abra o diretório do projeto:
   ```bash
   cd c:/codes/ia_candidata
   ```

2. Crie e ative o ambiente virtual:
   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .\.venv\Scripts\Activate.ps1
   # Linux/macOS:
   source .venv/bin/activate
   ```

3. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   # Para ferramentas de desenvolvimento e testes:
   pip install -r requirements-dev.txt
   ```

4. Instale os navegadores do Playwright:
   ```bash
   playwright install chromium
   ```

---

## 2. Configuração do Ambiente

1. **Variáveis de Ambiente (`.env`):**
   Copie `.env.example` para `.env` e configure suas credenciais:
   ```bash
   copy .env.example .env
   ```
   Principais variáveis:
   - `LLM_API_KEY`: Chave da API do LLM (OpenAI, OpenRouter, Gemini, Groq, etc.).
   - `LLM_BASE_URL`: Endpoint da API compatível com OpenAI (ex: `https://generativelanguage.googleapis.com/v1beta/openai/` para Gemini).
   - `LLM_MODEL`: Modelo utilizado (ex: `gemini-2.5-flash` ou `gpt-4o-mini`).
   - `GOOGLE_SERVICE_ACCOUNT_FILE`: Caminho para credencial de service account (`service_account.json`), se aplicável.
   - `DRIVE_FOLDER_ID`: ID da pasta do Google Drive com currículos.
   - `SHEET_ID`: ID da planilha do Google Sheets com a lista de vagas.

2. **Perfil do Candidato (`data/profile.json`):**
   Crie o arquivo de perfil a partir do exemplo fornecido:
   ```bash
   copy data\profile.example.json data\profile.json
   ```
   Preencha seus dados reais (nome, e-mail, telefone, links de portfólio, respostas base para perguntas frequentes e preferências de autodeclaração EEO).

---

## 3. Autenticação Google Drive / Sheets

Caso não utilize uma Service Account corporativa, realize a autenticação OAuth interativa (uma única vez):
```bash
python scripts/auth_google.py
```
Isso abrirá uma janela no navegador para autorizar o acesso à sua planilha e pasta de currículos, salvando o arquivo `token.json` localmente.

---

## 4. Execução do Sistema

O ponto de entrada principal é `main.py`.

### Execuções recomendadas

- **Dry-run simulado (padrão seguro — não clica em submit final):**
  ```bash
  python main.py --limit 3
  ```

- **Execução em lote a partir de planilha CSV local:**
  ```bash
  python main.py --source-csv data/vagas.csv --limit 5
  ```

- **Uso de motor estritamente baseado em regras (sem dependência do sidecar Laya):**
  ```bash
  python main.py --limit 1 --backend rules
  ```

- **Envio real (desativa simulação — realiza submissão efetiva no ATS):**
  ```bash
  python main.py --limit 1 --no-dry-run
  ```

### Flags e Opções Disponíveis

| Flag | Descrição | Padrão |
| :--- | :--- | :--- |
| `--limit N` | Limita o número de vagas a processar | Sem limite |
| `--source-csv PATH` | Caminho para planilha local (.xlsx ou .csv) em vez do Google Sheets | Google Sheets |
| `--no-dry-run` | Executa submissão real no formulário (por padrão roda em dry-run simulado) | `False` |
| `--backend {auto,laya,rules}` | Define o motor de decisão para triagem e checagens | `auto` |
| `--include-blank-status` | Inclui vagas com status em branco na planilha | `False` |
| `--ats {lever,greenhouse}` | Filtra vagas por ATS específica | Todas |
| `--concurrency N` | Limite de candidaturas paralelas | Configurado no `.env` |

---

## 5. Avaliação Estatística do Modelo Laya

O sistema inclui o sidecar Laya para decisões de triagem (DP1 a DP6). Para avaliar a calibração do modelo e liberar os pontos de decisão ativos:

1. Certifique-se de que o sidecar Laya está em execução (porta 8787).
2. Execute o script de avaliação estatística:
   ```bash
   python scripts/eval_decisions.py
   ```
O script verifica acurácia seletiva, baseline majoritário, ECE (Expected Calibration Error) e cobertura mínima sobre o conjunto de testes em `data/decision_goldens.json`, salvando o relatório em `artifacts/laya_eval_report.json`.

---

## 6. Qualidade de Código e Testes

Execute a suíte de testes unitários e de integração:
```bash
# Rodar todos os testes
pytest -q

# Apenas testes unitários rápidos
pytest tests/unit -q
```

Verificação estática de tipos e formatação:
```bash
# Linter (Ruff)
ruff check .

# Tipagem estática (Mypy)
mypy core config
```

---

## 7. Grafo de Conhecimento e Documentação (Padrão Google OKF)

A especificação técnica e arquitetura do projeto seguem o padrão **Open Knowledge Format (OKF v0.2 do Google Cloud)**, otimizado para leitura por engenheiros e agentes de IA:

- 📑 **Hub Mestre / Grafo de Navegação**: [`docs/okf/index.md`](docs/okf/index.md)
- 🏗️ **Arquitetura & Subsistemas**: [`docs/okf/architecture.md`](docs/okf/architecture.md)
- 🎯 **Camada de Decisão Calibrada (Laya & ECE)**: [`docs/okf/decision_engine.md`](docs/okf/decision_engine.md)
- 🛡️ **Políticas de Segurança e Invariantes Éticos**: [`docs/okf/security_and_ethics.md`](docs/okf/security_and_ethics.md)
- 📦 **Contratos de Dados & Schemas Pydantic**: [`docs/okf/data_contract.md`](docs/okf/data_contract.md)
- 🌐 **Diagnóstico de Prontidão Open Source**: [`docs/okf/open_source_readiness.md`](docs/okf/open_source_readiness.md)
- 📄 **Especificação Técnica Consolidada**: [`docs/OKF_SPECIFICATION.md`](docs/OKF_SPECIFICATION.md)

---

## 8. Aviso Legal e Uso Ético (Disclaimer)

> [!WARNING]
> **Aviso de Responsabilidade**:  
> Este software foi desenvolvido para fins estritamente **educacionais, de pesquisa em sistemas multi-agentes e automação de produtividade pessoal**.  
> O usuário assume total responsabilidade por:
> 1. Garantir que todas as informações submetidas a formulários representam com fidedignidade sua formação e experiência profissional real;
> 2. Cumprir integralmente os Termos de Serviço (ToS) das plataformas de recrutamento e empresas contratantes;
> 3. Monitorar e auditar os envios realizados, respeitando as políticas contra spam e abuso de requisições.

---

## 9. Licença

Distribuído sob a licença [MIT](LICENSE). Consulte `LICENSE` para mais informações.
