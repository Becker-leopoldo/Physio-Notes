---
description: Cria uma tela HTML para qualquer cliente POC, seguindo o design system do CLAUDE.md
argument-hint: "[NomeCliente] | [descrição da tela e requisitos]"
---

Você é o **Orquestrador de POC — HTML**. Gera telas HTML estáticas seguindo o design system definido em `CLAUDE.md`.

## Entrada

Argumento no formato: `[NomeCliente] | [descrição da tela e requisitos]`

Exemplos:
- `Dasa | Tela de importação com filtro por competência e tabela de lotes`
- `ClienteB | Tela de login com campos email e senha`

---

## Processo

### PASSO 1 — Descubra o contexto do cliente

Antes de qualquer geração, leia:
1. `CLAUDE.md` — design system global
2. `assets/styles.css` — classes disponíveis
3. Liste os arquivos em `{CLIENTE}/src/` (ou `{CLIENTE}/` se não houver src)
4. Leia o arquivo `home.html` ou equivalente do cliente para extrair:
   - Itens e hrefs da sidebar (`.sidebar-nav`)
   - Nome da brand (`.brand-name`)
   - Estrutura do sidebar-footer (usuário, avatar)
   - Padrões visuais já aplicados (classes customizadas no `<style>`)

Se a pasta do cliente **não existir ainda**, crie-a com subpasta `src/` e considere a sidebar vazia (apenas "Home").

### PASSO 2 — Planejamento

Informe ao usuário:
- Pasta de saída detectada: `{CLIENTE}/src/`
- Nome do arquivo: `tela-{nome}.html`
- Item da sidebar que ficará `active`
- Componentes que serão criados

### PASSO 3 — Gere Estrutura e Componentes em PARALELO

Dispare dois sub-agentes simultaneamente com `Agent tool` (`subagent_type: "general-purpose"`).

---

#### SUB-AGENTE A — Estrutura

```
Você é o Agente de Estrutura de POC HTML.

CLIENTE: {CLIENTE}
REQUISITOS: {REQUISITOS}
PASTA DE SAÍDA: {CLIENTE}/src/
CSS PATH: ../../assets/styles.css

CONTEXTO DO CLIENTE (extraído dos arquivos existentes):
- Brand name: {BRAND_NAME}
- Itens da sidebar: {SIDEBAR_ITEMS_COM_HREFS_E_SVGS}
- Sidebar-footer: {SIDEBAR_FOOTER_HTML}

SUA TAREFA:
Gere o HTML completo da estrutura:
- <!DOCTYPE html>, <head> com title, Google Fonts (DM Serif Display + DM Sans), styles.css
- .app-layout > .sidebar + .main-content
- Sidebar idêntica ao padrão existente do cliente (mesmos itens, mesmos SVGs)
- Item correspondente à tela atual com classe "active" (ou nenhum se for sub-tela)
- .page-header com h1.page-title (font-family via classe .page-title) e p.page-subtitle
- .page-body com comentário <!-- COMPONENTES AQUI -->
- <footer class="powered-bar">powered by <strong>up it</strong></footer> antes de fechar </main>

REGRAS:
- Apenas variáveis CSS (--color-*, --space-*, --font-*)
- Nenhum valor hardcoded
- Nenhum style inline nos elementos HTML (use bloco <style> se necessário)

Retorne APENAS o HTML completo. Sem explicações.
```

---

#### SUB-AGENTE B — Componentes

```
Você é o Agente de Componentes de POC HTML.

CLIENTE: {CLIENTE}
REQUISITOS: {REQUISITOS}

CLASSES DISPONÍVEIS (assets/styles.css):
- Métricas: .metrics-grid, .card.card-metric, .metric-label, .metric-value, .metric-delta.positive/.negative
- Cards: .card, .card-header, .card-title, .card-subtitle, .card-body, .card-footer
- Tabela: .table-wrapper > .table, .text-right
- Badges: .badge.badge-{success|warning|danger|info|neutral}
- Botões: .btn.btn-{primary|secondary|ghost|danger} + .btn-sm/.btn-lg
- Formulários: .form-group, .form-label, .form-input, .form-select, .form-textarea, .form-hint
- Alertas: .alert.alert-{success|warning|danger|info}
- Seções: .section, .section-title
- Utilitários: .flex, .items-center, .justify-between, .gap-{2|3|4|6|8}, .mt-{4|6|8}, .mb-{4|6|8}, .text-sm, .text-xs, .text-muted, .font-semibold
- Layout header: .page-header-row (flex com justify-between)

VARIÁVEIS CSS: --color-*, --space-1 a --space-16, --radius-*, --shadow-*, --font-body/display, --text-xs a --text-4xl

SUA TAREFA:
Gere o HTML interno do .page-body com base nos requisitos.

REGRAS:
- Dados de exemplo realistas para o domínio do cliente
- Nenhum valor hardcoded de cor ou espaçamento em style=""
- Espaçamento entre seções: .mt-8 ou margin-top: var(--space-8)
- Nenhum accordion, collapse ou elemento que esconda conteúdo
- Padding dos cards ≥ var(--space-6)
- Se houver filtros, agrupe-os em um .card antes do conteúdo principal

Retorne APENAS o HTML interno do .page-body. Sem explicações.
```

---

### PASSO 4 — Monte e salve

1. Substitua `<!-- COMPONENTES AQUI -->` pelo output do Agente B no HTML do Agente A
2. Salve em `{CLIENTE}/src/tela-{nome}.html`

### PASSO 5 — QA

Dispare sub-agente de QA:

```
Você é o Agente de QA de POC HTML.

LEIA: CLAUDE.md, assets/styles.css, {CLIENTE}/src/{ARQUIVO}

REQUISITOS ORIGINAIS: {REQUISITOS}

## CAMADA 1 — Técnica

- [ ] Importa ../../assets/styles.css (caminho correto)
- [ ] Importa DM Serif Display + DM Sans do Google Fonts
- [ ] h1/.page-title usa font-family via classe CSS (não inline)
- [ ] Nenhum valor hardcoded de cor em atributo style=""
- [ ] .app-layout > .sidebar + .main-content presente
- [ ] Sidebar com itens de navegação e SVGs
- [ ] sidebar-footer com usuário presente
- [ ] Botão .sidebar-toggle presente dentro de .sidebar-brand
- [ ] CSS de sidebar recolhível incluído no <style> da tela
- [ ] Script de toggle presente antes de </body>
- [ ] Componentes usam classes corretas do design system
- [ ] Nenhum accordion ou elemento colapsável
- [ ] Footer .powered-bar com "powered by up it" presente
- [ ] Padding dos cards ≥ var(--space-6)

## CAMADA 2 — Funcional

- [ ] Todos os elementos dos requisitos foram implementados?
- [ ] Dados de exemplo fazem sentido para o domínio do cliente?
- [ ] Título e subtitle refletem o requisito corretamente?

Retorne: ✅ QA APROVADO ou ❌ QA REPROVADO com lista de problemas.
```

### PASSO 6 — UX Review

Após QA aprovado, dispare o sub-agente de UX:

```
Você é o Agente de UX Review de POC. Avalie a qualidade da experiência do usuário da tela gerada.

LEIA OBRIGATORIAMENTE:
- CLAUDE.md (design system e contexto do projeto)
- {CLIENTE}/src/{ARQUIVO} (tela a avaliar)
- Outros HTMLs do cliente em {CLIENTE}/src/ (para avaliar consistência)

REQUISITOS ORIGINAIS DA TELA: {REQUISITOS}

## CRITÉRIOS DE AVALIAÇÃO

Para cada item, classifique como:
- ✅ OK — sem problemas
- ⚠️ ATENÇÃO — pode melhorar
- ❌ CRÍTICO — prejudica o uso

### 1. Hierarquia Visual
- O título da página comunica claramente o propósito?
- O olho sabe onde ir primeiro (título → ação principal → conteúdo)?
- KPIs ou informações mais importantes estão em destaque?

### 2. Clareza de Ações
- Os botões e CTAs são óbvios e descritivos?
- Há uma ação principal clara na tela?
- Ações destrutivas (excluir, cancelar) estão visualmente diferenciadas?

### 3. Densidade de Informação
- A tela tem informação demais para o objetivo?
- Falta alguma informação essencial para o usuário tomar uma decisão?
- Tabelas têm colunas desnecessárias ou faltam colunas importantes?

### 4. Consistência
- O padrão visual bate com as outras telas do cliente?
- Labels e terminologia são consistentes com o domínio?
- Os badges de status seguem o mesmo critério de cores das outras telas?

### 5. Feedback ao Usuário
- Estados vazios estão tratados (tabela sem dados, filtro sem resultado)?
- O usuário sabe em que etapa/status o sistema está?
- Há indicação clara de qual item da sidebar está ativo?

### 6. Acessibilidade Básica
- Textos secundários têm contraste suficiente (não são apenas --color-text-muted)?
- Elementos interativos são claramente identificáveis como clicáveis?
- Labels de formulário estão associados aos campos?

## RESULTADO

Formato obrigatório:

UX REVIEW — [Nome da Tela]

| Critério | Status | Observação |
|----------|--------|------------|
| Hierarquia Visual | ✅/⚠️/❌ | ... |
| Clareza de Ações | ✅/⚠️/❌ | ... |
| Densidade | ✅/⚠️/❌ | ... |
| Consistência | ✅/⚠️/❌ | ... |
| Feedback | ✅/⚠️/❌ | ... |
| Acessibilidade | ✅/⚠️/❌ | ... |

PONTOS DE MELHORIA (apenas os ⚠️ e ❌):
- [item concreto e acionável]

VEREDICTO GERAL: APROVADO / APROVADO COM RESSALVAS / REPROVADO
```

Se o UX Review retornar itens ❌ **CRÍTICO**: aplique as correções diretamente no arquivo HTML e informe o usuário das mudanças feitas.

Se retornar apenas ⚠️ **ATENÇÃO**: apresente ao usuário e pergunte se quer aplicar as sugestões.

### PASSO 7 — Conclusão

- Informe caminho do arquivo, resultado do QA e resultado do UX Review
- Pergunte sobre a próxima tela

---

## Formato de resposta

```
Criando: [Nome da Tela] — [Cliente]
Arquivo: [cliente]/src/[arquivo].html
Contexto detectado: [N itens na sidebar, brand: X]

Gerando estrutura e componentes em paralelo...
HTML montado.
Executando QA técnico...   → [✅ aprovado / ❌ N problemas corrigidos]
Executando UX Review...    → [✅ aprovado / ⚠️ N sugestões / ❌ N críticos corrigidos]

Arquivo: [caminho]
Próxima tela?
```

---

## Clientes registrados

| Cliente | Pasta | Domínio |
|---------|-------|---------|
| Dasa | `Dasa/src/` | Comissionamento médico — sidebar: Home, Importação de Dados, Parametrização, Motor de Cálculo, Reprocessamento, Demonstrativos |

> Novos clientes são descobertos automaticamente lendo os arquivos existentes. Adicione uma linha aqui após criar o primeiro arquivo de um novo cliente.
