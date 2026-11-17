---
type: DesignSystemSpecification
title: Candidata // Protocol — Sistema de Design e Tokens Visuais
description: Especificação de design tokens, paleta cromática, tipografia tabular e assets da interface de telemetria.
resource: web/static/app.css
tags:
  - design-system
  - visual-identity
  - ui-tokens
  - branding
  - studio-edition
  - quantum-stealth
timestamp: "2026-10-12T18:40:00Z"
---

# Candidata // Protocol — Design System & Tokens Visuais

> **Padrão de Especificação**: Open Knowledge Format (OKF v0.2)  
> **Nome da Identidade**: `Candidata // Protocol (Telemetry Dark UI)`  
> **Artefatos Chave**: `web/static/app.css`, `web/static/assets/logo.svg`, `web/static/assets/favicon.svg`, `web/static/assets/brand_emblem.jpg`  
> **Objetivo**: Fornecer uma linguagem visual consistente, legível e orientada a telemetria para monitoramento em tempo real de pipelines assíncronos.

---

## 1. Filosofia de Design

A interface foi projetada com foco em **densidade informacional, contraste elevado e legibilidade numérica**, evitando elementos puramente decorativos em favor de indicadores de estado claros:
- Superfícies escuras de baixo contraste de brilho (`#06080B` / `#0B0F15`) com bordas nítidas de `1px`.
- Vetores SVG geométricos e indicadores semânticos de status no lugar de ícones informais.
- Tipografia tabular (`JetBrains Mono` com `tabular-nums`) para evitar oscilação de layout em atualizações ao vivo via SSE.

### Princípios Norteadores
1. **Engenharia de Missão Crítica**: A interface deve passar a confiança de um terminal de controle de satélites ou de trading algorítmico.
2. **Auditabilidade Imediata**: Cada milissegundo, token e arquivo manipulado é tratado como dado de instrumentação científica.
3. **Ergonomia e Baixa Fadiga Ocular**: Fundos ultra-escuros com taxas de contraste que atendem rigorosamente WCAG AAA para texto e controles interativos.

---

## 2. Paleta Cromática e Tokens Semânticos

O sistema utiliza variáveis CSS puras definidas na raiz (`:root`), desacopladas de qualquer framework externo:

### 2.1 Superfícies e Profundidade (Obsidian & Carbon)

| Token CSS | Hex / Valor | Uso Semântico |
| :--- | :--- | :--- |
| `--bg-void` | `#06080B` | Fundo base global da janela e canvas principal |
| `--bg-surface` | `#0B0F15` | Superfície secundária (sidebar, top bar, drawers) |
| `--bg-card` | `#101620` | Superfície de cartões, painéis e contêineres métricos |
| `--bg-elevated` | `#16202E` | Modais, menus flutuantes e tooltips elevados |
| `--border-subtle`| `#1E293B` | Divisores, grades estruturais e bordas de cards |
| `--border-bright`| `#334155` | Bordas ativas em hover e campos de foco |

### 2.2 Cores de Acento e Emissão de Luz

| Token CSS | Hex / Valor | Propósito |
| :--- | :--- | :--- |
| `--mint` | `#00F29D` | **Cor Primária da Marca**: Sucesso, taxa de conversão, estados ativos e pulso sonar |
| `--mint-glow` | `rgba(0, 242, 157, 0.15)` | Halos de foco, anéis de progresso e botões primários |
| `--cyan` | `#38BDF8` | **Acento Secundário**: Telemetria, tokens de IA, chamadas de rede e links ativos |
| `--cyan-glow` | `rgba(56, 189, 248, 0.15)` | Destaques de dados analíticos e badges informativos |
| `--amber` | `#F59E0B` | **Atenção / Revisão**: Ações que exigem revisão humana, testes pendentes e avisos |
| `--rose` | `#F43F5E` | **Falha / Bloqueio**: Erros de execução, limites de cota e interrupções do robô |
| `--purple` | `#818CF8` | **Decisão Laya / Sidecar**: Indicadores de inferência bayesiana e estatística |

### 2.3 Tipografia e Escala de Leitura

| Token CSS | Hex / Valor | Função |
| :--- | :--- | :--- |
| `--text-primary` | `#F8FAFC` | Títulos, cabeçalhos, números grandes e textos de ênfase |
| `--text-secondary`| `#94A3B8` | Textos corridos, rótulos de campos e descrições |
| `--text-muted` | `#64748B` | Metadados secundários, timestamps e notas de rodapé |

---

## 3. Tipografia e Numeração Tabular

O sistema emprega duas famílias tipográficas interdependentes:

1. **Interface & Prose**: `'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`
   - Geometria limpa, kerning otimizado para telas de alta resolução e excelente legibilidade em tamanhos reduzidos (11px a 14px).
2. **Telemetria, Código & Números**: `'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace`
   - Utilizado em **todas** as métricas quantitativas (tokens, segundos, contadores, hashes de arquivo).
   - Ativação obrigatória de `font-variant-numeric: tabular-nums` para impedir oscilação visual durante atualizações em tempo real via SSE.

---

## 4. Inventário de Assets Visuais da Marca

Todos os assets proprietários estão armazenados em `web/static/assets/`:

### 4.1 Logotipo e Vetor Mestre (`logo.svg`)
- **Conceito**: Escudo hexagonal de abertura quântica (*Quantum Aperture Shield*).
- **Geometria**:
  - Escudo exterior em Cyan com borda translúcida.
  - Retículo central em Quantum Mint simbolizando foco e precisão de mira em vagas de alta aderência.
  - Três nós orbitais representando os três pilares do sistema: *Playwright (Automação)*, *Laya (Decisão)* e *LLM (Assistência Ética)*.
- **Formato**: SVG nativo puro, escalável de 16px a 4K sem perda de definição, pesando menos de 1.5 KB.

### 4.2 Favicon do Navegador (`favicon.svg`)
- Versão condensada de alta taxa de contraste em 32x32px com gradiente de alta intensidade (`#00F29D` para `#38BDF8`) sobre fundo Dark Charcoal (`#06080B`), garantindo visibilidade mesmo em abas escuras.

### 4.3 Emblema Render 3D Studio (`brand_emblem.jpg`)
- Renderização hiper-detalhada da marca: Escudo de titânio acetinado e fibra de carbono fosca, dotado de iluminação interna luminescente verde menta e ciano cibernético, emitindo um suave flare anamórfico e reflexos de estúdio.
- Integrado na aba de apresentação institucional do painel.

---

## 5. Biblioteca de Componentes e Micro-Interações

### 5.1 Fundo Matriz de Pontos (*Dot Matrix Canvas*)
A textura de fundo cria profundidade sem interferir na leitura:
```css
body {
  background-color: var(--bg-void);
  background-image: radial-gradient(circle, #1e293b 1px, transparent 1px);
  background-size: 24px 24px;
}
```

### 5.2 Pulso Sonar de Execução Ativa
Quando o harness está em operação, o card de status emite um anel de pulso sonar contínuo indicando que o processo não está congelado:
```css
@keyframes sonar-pulse {
  0% { transform: scale(1); opacity: 0.8; }
  70% { transform: scale(1.4); opacity: 0; }
  100% { transform: scale(1); opacity: 0; }
}
```

### 5.3 Micro-LEDs de Status
Substituem completamente emojis em tabelas e cabeçalhos:
- **Verde Menta (`--mint`)**: Executando / Sucesso
- **Âmbar Solar (`--amber`)**: Pausado / Revisão Manual Necessária
- **Carmesim (`--rose`)**: Falha Crítica / Conexão Perdida
- **Ciano (`--cyan`)**: Parado / Aguardando Comando

### 5.4 Lightbox de Evidências Visuais
Capturas de tela das páginas durante erros ou aprovações são exibidas em um modal com moldura de alta precisão, backdrop blur e metadados contextuais (código da vaga, timestamp e etapa do formulário).

---

## 6. Verificação de Aderência e Testabilidade

Para auditar o sistema de design em tempo de execução:
1. Abra o painel web em `http://127.0.0.1:8000`.
2. Clique na aba **"Design System"** na barra de navegação superior.
3. Esta aba renderiza:
   - Todas as amostras de cores ativas com seus respectivos códigos hexadecimais e tokens CSS.
   - Amostras de tipografia comparando *Plus Jakarta Sans* e *JetBrains Mono*.
   - A vitrine de botões, inputs, cards e chips de status.
   - O emblema oficial da marca com visualização em alta resolução.
