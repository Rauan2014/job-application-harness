---
type: DesignSystemSpecification
title: Candidata // Protocol — Sistema de Design e Identidade Visual (Studio Edition)
description: Especificação canônica da identidade visual única, design system, tokens semânticos, paleta cromática e inventário de assets do ecossistema Candidata IA.
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

# Candidata // Protocol — Sistema de Design e Identidade Visual (Studio Edition)

> **Padrão de Especificação**: Open Knowledge Format (Google Cloud, v0.2)  
> **Nome da Identidade**: `Candidata // Protocol (Studio Edition / Quantum Stealth)`  
> **Artefatos Chave**: `web/static/app.css`, `web/static/assets/logo.svg`, `web/static/assets/favicon.svg`, `web/static/assets/brand_emblem.jpg`  
> **Objetivo**: Fornecer uma linguagem visual única, profissional e telemetrizada, erradicando clichês genéricos de "vibe code" / "AI wrappers" e entregando uma experiência de cockpit aeroespacial para automação autônoma de carreira.

---

## 1. Filosofia de Design e Ruptura com o "Vibe Code"

A maioria dos utilitários modernos de IA sofre de saturação estética genérica ("vibe code"), caracterizada por:
- Ícones em formato de emojis infantis (🤖, 🚀, 💡, 📊).
- Gradientes roxos/violetas padronizados de templates sem identidade.
- Cards flutuantes com sombras exageradas e cantos excessivamente arredondados.
- Tipografia vaga sem suporte a números tabulares para telemetria.

O **Candidata // Protocol** rompe categoricamente com esse padrão:

```
[VIBE CODE GENÉRICO]                 [CANDIDATA // PROTOCOL]
Gradientes roxos padronizados   -->  Obsidian Carbon + Quantum Mint + Cyber Cyan
Emojis em textos e botões       -->  Micro-vetores SVG geométricos e LEDs semânticos
Sombras difusas e imprecisas    -->  Bordas nítidas de 1px com iluminação tátil
Layouts puramente decorativos   -->  Cockpit de telemetria densa com JetBrains Mono
```

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
