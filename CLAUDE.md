# POC Design System — CLAUDE.md

Este arquivo define o padrão visual e de desenvolvimento para todas as telas de POC neste projeto.
**Leia e siga rigorosamente antes de criar ou editar qualquer arquivo HTML.**

## Regra de Manutenção do LEIA-ME.txt

**Sempre que qualquer arquivo `.md` do projeto for criado ou modificado, o `LEIA-ME.txt` deve ser atualizado para refletir a mudança.**

Isso inclui: `CLAUDE.md`, arquivos em `.claude/commands/`, ou qualquer outro `.md` adicionado futuramente.

O `LEIA-ME.txt` é o guia humano do projeto — deve estar sempre sincronizado com a realidade dos arquivos de configuração.

---

## Regra de Manutenção do DOC.md

**Sempre que qualquer arquivo de um subprojeto for criado, modificado ou removido, o `DOC.md` desse subprojeto deve ser atualizado para refletir a mudança.**

Isso se aplica a qualquer subpasta que possua um `DOC.md` (ex: `Physio Notes/DOC.md`).

O que atualizar:
- Se um arquivo foi **criado**: adicionar linha na tabela da seção correspondente (Backend, Frontend ou Raiz) com nome e descrição
- Se um arquivo foi **modificado**: revisar a descrição existente se o comportamento ou propósito mudou
- Se um arquivo foi **removido**: retirar a linha da tabela
- Se uma **variável de ambiente** foi adicionada ou removida: atualizar a tabela de variáveis no `DOC.md`
- Se o **modelo de IA, stack ou fluxo de deploy** mudou: atualizar as seções correspondentes

O `DOC.md` é a documentação viva do projeto — deve refletir sempre o estado atual do código.

---

## Estrutura de Pastas

```
POC/
├── CLAUDE.md              ← este arquivo (raiz, lido automaticamente)
├── assets/
│   ├── styles.css         ← CSS global compartilhado (importar em todas as telas)
│   └── fonts.css          ← imports das fontes Google
├── Dasa/
│   ├── tela-dashboard.html
│   ├── tela-relatorios.html
│   └── ...
├── ClienteB/
│   └── ...
```

Cada cliente tem sua própria subpasta. Todas as telas importam `../../assets/styles.css`.

---

## Design System

### Paleta de Cores

```css
--color-bg:        #F9F8F6;   /* off-white — fundo principal */
--color-surface:   #FFFFFF;   /* branco puro — cards, painéis */
--color-border:    #E8E6E1;   /* borda sutil */
--color-border-strong: #D0CEC9; /* borda com mais presença */

--color-text:      #1A1A1A;   /* texto principal — quase preto */
--color-text-secondary: #6B6860; /* texto secundário — grafite médio */
--color-text-muted: #A09D97;  /* texto desabilitado / placeholder */

--color-accent:    #1A1A1A;   /* acento primário — preto/grafite */
--color-accent-hover: #333333;
--color-accent-light: #F0EFED; /* fundo de hover em elementos sutis */

--color-success:   #2D6A4F;
--color-warning:   #92600A;
--color-danger:    #9B1C1C;
--color-info:      #1E3A5F;

--color-success-bg: #EDF7F2;
--color-warning-bg: #FEF7EC;
--color-danger-bg:  #FEF2F2;
--color-info-bg:    #EFF6FF;
```

### Tipografia

Fontes: **DM Serif Display** (títulos) + **DM Sans** (corpo e UI)

```html
<!-- Importar no <head> de cada tela -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
```

```css
--font-display: 'DM Serif Display', Georgia, serif;
--font-body:    'DM Sans', system-ui, sans-serif;

/* Escala tipográfica */
--text-xs:   0.75rem;    /* 12px */
--text-sm:   0.875rem;   /* 14px */
--text-base: 1rem;       /* 16px */
--text-lg:   1.125rem;   /* 18px */
--text-xl:   1.25rem;    /* 20px */
--text-2xl:  1.5rem;     /* 24px */
--text-3xl:  1.875rem;   /* 30px */
--text-4xl:  2.25rem;    /* 36px */
```

**Uso:**
- `font-family: var(--font-display)` → títulos de página (h1, h2 principais)
- `font-family: var(--font-body)` → tudo mais (labels, botões, tabelas, parágrafos)

### Espaçamento

```css
--space-1:  0.25rem;   /*  4px */
--space-2:  0.5rem;    /*  8px */
--space-3:  0.75rem;   /* 12px */
--space-4:  1rem;      /* 16px */
--space-5:  1.25rem;   /* 20px */
--space-6:  1.5rem;    /* 24px */
--space-8:  2rem;      /* 32px */
--space-10: 2.5rem;    /* 40px */
--space-12: 3rem;      /* 48px */
--space-16: 4rem;      /* 64px */
```

### Bordas e Raios

```css
--radius-sm:  4px;
--radius-md:  8px;
--radius-lg:  12px;
--radius-xl:  16px;
--radius-full: 9999px;

--border: 1px solid var(--color-border);
--border-strong: 1px solid var(--color-border-strong);
```

### Sombras

Sombras sutis. Nunca usar box-shadow pesado ou colorido.

```css
--shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
--shadow-md: 0 2px 8px rgba(0,0,0,0.07);
--shadow-lg: 0 4px 16px rgba(0,0,0,0.08);
```

---

## Componentes Padrão

### Layout de Página

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Nome da Tela — POC [Cliente]</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="../../assets/styles.css">
</head>
<body>
  <div class="app-layout">
    <aside class="sidebar"><!-- navegação lateral --></aside>
    <main class="main-content">
      <header class="page-header">
        <h1 class="page-title">Título da Tela</h1>
        <p class="page-subtitle">Descrição opcional</p>
      </header>
      <section class="page-body">
        <!-- conteúdo principal -->
      </section>
    </main>
  </div>
</body>
</html>
```

### Botões

```html
<!-- Primário (fundo preto) -->
<button class="btn btn-primary">Confirmar</button>

<!-- Secundário (borda, fundo transparente) -->
<button class="btn btn-secondary">Cancelar</button>

<!-- Ghost (sem borda, só texto) -->
<button class="btn btn-ghost">Ver detalhes</button>

<!-- Destrutivo -->
<button class="btn btn-danger">Excluir</button>

<!-- Tamanhos -->
<button class="btn btn-primary btn-sm">Pequeno</button>
<button class="btn btn-primary btn-lg">Grande</button>
```

### Cards

```html
<!-- Card simples -->
<div class="card">
  <div class="card-header">
    <h3 class="card-title">Título</h3>
    <span class="card-subtitle">Subtítulo</span>
  </div>
  <div class="card-body">
    <!-- conteúdo -->
  </div>
  <div class="card-footer">
    <!-- ações -->
  </div>
</div>

<!-- Card de métrica (KPI) -->
<div class="card card-metric">
  <span class="metric-label">Total de Avaliações</span>
  <span class="metric-value">1.284</span>
  <span class="metric-delta positive">+12% este mês</span>
</div>
```

### Inputs e Formulários

```html
<div class="form-group">
  <label class="form-label" for="campo">Nome do Campo</label>
  <input class="form-input" type="text" id="campo" placeholder="Placeholder">
  <span class="form-hint">Texto de apoio opcional</span>
</div>

<!-- Select -->
<div class="form-group">
  <label class="form-label">Categoria</label>
  <select class="form-select">
    <option>Selecione...</option>
  </select>
</div>

<!-- Textarea -->
<div class="form-group">
  <label class="form-label">Observações</label>
  <textarea class="form-textarea" rows="4" placeholder="Digite aqui..."></textarea>
</div>
```

### Tabelas

```html
<div class="table-wrapper">
  <table class="table">
    <thead>
      <tr>
        <th>Coluna A</th>
        <th>Coluna B</th>
        <th class="text-right">Ações</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Dado</td>
        <td>Dado</td>
        <td class="text-right">
          <button class="btn btn-ghost btn-sm">Editar</button>
        </td>
      </tr>
    </tbody>
  </table>
</div>
```

### Badges / Status

```html
<span class="badge badge-success">Aprovado</span>
<span class="badge badge-warning">Pendente</span>
<span class="badge badge-danger">Rejeitado</span>
<span class="badge badge-info">Em análise</span>
<span class="badge badge-neutral">Rascunho</span>
```

### Navegação Lateral (Sidebar)

```html
<aside class="sidebar">
  <div class="sidebar-brand">
    <span class="brand-name">POC [Cliente]</span>
  </div>
  <nav class="sidebar-nav">
    <a href="#" class="nav-item active">Dashboard</a>
    <a href="#" class="nav-item">Relatórios</a>
    <a href="#" class="nav-item">Configurações</a>
  </nav>
</aside>
```

---

## Regras Invioláveis

### Footer obrigatório

Toda tela deve ter o rodapé `powered by up it` ao final do `.main-content`:

```html
<footer class="powered-bar">powered by <strong>up it</strong></footer>
```

A classe `.powered-bar` já está definida no `styles.css`.

### Sidebar recolhível obrigatória

Toda tela que possua menu lateral (`.sidebar` com `.sidebar-nav`) deve incluir o botão de recolher e o CSS/JS correspondente.

**Botão — dentro de `.sidebar-brand`:**
```html
<div class="sidebar-brand">
  <span class="brand-name">NomeCliente</span>
  <button class="sidebar-toggle" id="sidebarToggle" title="Recolher menu">
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M7.5 2L3.5 6l4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  </button>
</div>
```

**CSS — adicionar no `<style>` da tela:**
```css
.sidebar-toggle {
  position: absolute;
  top: var(--space-5);
  right: calc(-1 * var(--space-4));
  width: 24px; height: 24px;
  border-radius: var(--radius-full);
  background: var(--color-surface);
  border: var(--border-strong);
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; z-index: 101;
  transition: background 0.15s;
  box-shadow: var(--shadow-sm);
}
.sidebar-toggle:hover { background: var(--color-accent-light); }
.sidebar-toggle svg { transition: transform 0.25s; }
.sidebar-brand { position: relative; }
.sidebar { transition: width 0.25s ease; overflow: hidden; }

.sidebar.collapsed { width: 56px; }
.sidebar.collapsed .brand-name,
.sidebar.collapsed .nav-section-label,
.sidebar.collapsed .user-name,
.sidebar.collapsed .user-role { display: none; }
.sidebar.collapsed .nav-item { justify-content: center; padding: var(--space-2); font-size: 0; gap: 0; }
.sidebar.collapsed .nav-item svg { width: 16px; height: 16px; flex-shrink: 0; }
.sidebar.collapsed .sidebar-brand { padding: 0 var(--space-3) var(--space-6); }
.sidebar.collapsed .sidebar-footer { padding: var(--space-4) var(--space-3); }
.sidebar.collapsed .user-info { justify-content: center; }
.sidebar.collapsed .sidebar-toggle svg { transform: rotate(180deg); }
.sidebar.collapsed ~ .main-content {
  flex: 0 0 auto;
  width: calc(100% - 56px);
  margin-left: auto;
  margin-right: auto;
}
```

**JS — antes de `</body>`:**
```html
<script>
  const sidebar = document.querySelector('.sidebar');
  const toggle = document.getElementById('sidebarToggle');
  toggle.addEventListener('click', () => sidebar.classList.toggle('collapsed'));

  const userTrigger = document.getElementById('userMenuTrigger');
  const userMenu = document.getElementById('userMenu');
  userTrigger.addEventListener('click', (e) => { e.stopPropagation(); userMenu.classList.toggle('open'); });
  document.addEventListener('click', () => userMenu.classList.remove('open'));
</script>
```

---

### Menu do Usuário (logout)

Toda tela com sidebar deve ter um menu de contexto no clique do usuário logado, com opção de **Sair**.

**HTML — dentro de `.sidebar-footer`:**
```html
<div class="sidebar-footer">
  <div class="user-menu" id="userMenu">
    <a href="login.html" class="user-menu-item user-menu-item-danger">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M6 2H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3M11 11l3-3-3-3M14 8H6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      Sair
    </a>
  </div>
  <div class="user-info user-menu-trigger" id="userMenuTrigger">
    <div class="user-avatar">XX</div>
    <div>
      <div class="user-name">Nome do Usuário</div>
      <div class="user-role">Perfil</div>
    </div>
  </div>
</div>
```

**CSS — no `<style>` da tela:**
```css
.sidebar-footer { position: relative; }
.user-menu-trigger {
  cursor: pointer;
  border-radius: var(--radius-md);
  padding: var(--space-2);
  margin: calc(-1 * var(--space-2));
  transition: background 0.15s;
  width: calc(100% + var(--space-4));
}
.user-menu-trigger:hover { background: var(--color-accent-light); }
.user-menu {
  position: absolute;
  bottom: calc(100% + var(--space-2));
  left: 0; right: 0;
  background: var(--color-surface);
  border: var(--border-strong);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  padding: var(--space-2);
  display: none;
  z-index: 200;
}
.user-menu.open { display: block; }
.user-menu-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: var(--color-text);
  text-decoration: none;
  transition: background 0.15s;
  cursor: pointer;
  background: none;
  border: none;
  width: 100%;
  font-family: var(--font-body);
}
.user-menu-item:hover { background: var(--color-accent-light); }
.user-menu-item-danger { color: var(--color-danger); }
.user-menu-item-danger:hover { background: var(--color-danger-bg); }
.sidebar.collapsed .user-menu { left: 0; right: auto; min-width: 160px; }
```

---

## Regra de Versionamento — Physio Notes

**A cada commit de deploy no subprojeto `Physio Notes/`, a versão do app DEVE ser incrementada.**

- Constante localizada em `Physio Notes/frontend/index.html`:
  ```js
  const APP_VERSION = 'Beta-0.XX'; // bump a cada deploy
  ```
- Incrementar o sufixo: `Beta-0.12` → `Beta-0.13` → `Beta-0.14` etc.
- O commit de bump deve ser separado ou incluído no commit principal, com mensagem `chore(physio-notes): bump versão Beta-0.XX`.
- **Nunca fazer deploy sem bumpar a versão** — é o principal controle de versão para o testador.
- A cada nova versão, **atualizar `Physio Notes/CHANGELOG.md`** com as mudanças da release antes do commit, seguindo o formato existente (`## Beta-0.XX — YYYY-MM-DD` + seções Funcionalidades / Melhorias / Correções).

---

### ✅ Sempre fazer
- Importar `styles.css` global em toda tela
- Usar variáveis CSS (`--color-*`, `--space-*`, etc.) — nunca valores hardcoded
- Usar `font-family: var(--font-body)` como padrão geral
- Reservar `var(--font-display)` apenas para o h1/título principal da página
- Manter fundo `var(--color-bg)` no body
- Exibir todos os textos de detalhe inline (sem accordion, sem collapse)
- Espaçamento generoso — prefira `--space-8` ou mais entre seções
- Incluir `<footer class="powered-bar">powered by <strong>up it</strong></footer>` ao final de cada `.main-content`
- Incluir menu de logout no clique do usuário em toda tela com sidebar (padrão `.user-menu-trigger` + `.user-menu`)
- Usar **sempre os pickers customizados** para filtros de mês/ano e listas — nunca `<select>` nativo (ver seção abaixo)
- **Labels de filtro** (ex: "Competência", "Paciente") devem ser sempre **centralizados e em negrito**: `style="text-align:center;font-weight:600;"`

### ❌ Nunca fazer
- Sombras coloridas ou pesadas
- Gradientes (exceto sutilíssimos, quase imperceptíveis)
- Bordas grossas (> 1px)
- Cores vibrantes fora da paleta definida
- Fontes diferentes das definidas (sem Arial, Roboto, Inter, etc.)
- Accordions ou elementos colapsáveis para conteúdo informacional
- Animações chamativas ou desnecessárias
- Padding interno de card menor que `--space-6`
- **`<select>` nativo** para filtros visíveis ao usuário — usar sempre `criarPickerMesAno` ou `criarPickerLista`

---

## Pickers Customizados (substitutos do `<select>` nativo)

Nunca usar `<select class="form-select">` para filtros visíveis ao usuário. Sempre usar os dois componentes abaixo, que seguem o visual do design system (borda sutil, painel flutuante arredondado, item selecionado em fundo preto).

---

### `criarPickerMesAno(valorInicial, onChange, opções)`

Para filtros de mês/ano (competência, período de billing, etc.).

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `valorInicial` | `string \| ''` | Valor inicial no formato `'YYYY-MM'` ou `''` para "todos" |
| `onChange` | `(valor: string) => void` | Chamado com `'YYYY-MM'` ou `''` ao selecionar |
| `opções.allowAll` | `boolean` | Exibe botão "Todos os meses" (padrão: `false`) |
| `opções.fullWidth` | `boolean` | Botão ocupa 100% do container (padrão: `false`) |

Retorna um elemento DOM com `.getValue()` → `string`.

**Uso típico:**
```html
<div id="meu-picker-slot"></div>
```
```js
const picker = criarPickerMesAno('2026-04', (mes) => {
  recarregarDados(mes || null);
}, { allowAll: true, fullWidth: true });
document.getElementById('meu-picker-slot').appendChild(picker);

// Ler valor atual:
picker.getValue(); // → 'YYYY-MM' ou ''
```

---

### `criarPickerLista(opcoes, valorInicial, onChange, opções)`

Para qualquer lista plana de opções (pacientes, categorias, status, etc.).

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `opcoes` | `{value: string, label: string}[]` | Lista de opções; inclua `{value:'', label:'Todos os ...'}` como primeira opção para "sem filtro" |
| `valorInicial` | `string` | Valor inicial (`value` da opção selecionada) |
| `onChange` | `(valor: string) => void` | Chamado com o `value` da opção ao selecionar |
| `opções.fullWidth` | `boolean` | Botão ocupa 100% do container (padrão: `false`) |

Retorna um elemento DOM com `.getValue()` e `.setValue(v)`.

**Uso típico:**
```html
<div id="meu-picker-pac-slot"></div>
```
```js
const opcoes = [
  { value: '', label: 'Todos os pacientes' },
  { value: '1', label: 'Ana Silva' },
  { value: '2', label: 'João Souza' },
];
const picker = criarPickerLista(opcoes, '', (pacId) => {
  recarregarDados(pacId || null);
}, { fullWidth: true });
document.getElementById('meu-picker-pac-slot').appendChild(picker);
```

---

### Padrão de filtros em par (grid 2 colunas)

Quando há dois filtros lado a lado (ex: Competência + Paciente), usar sempre:

```html
<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-3);margin-bottom:var(--space-5);">
  <div class="form-group" style="margin:0;">
    <label class="form-label">Competência</label>
    <div id="picker-mes-slot"></div>
  </div>
  <div class="form-group" style="margin:0;">
    <label class="form-label">Paciente</label>
    <div id="picker-pac-slot"></div>
  </div>
</div>
```

---

## Como Criar uma Nova Tela

1. Copie o template de layout de página acima
2. Ajuste o `<title>` e o `<h1>`
3. Use os componentes deste arquivo conforme necessário
4. Importe sempre `../../assets/styles.css` (ajuste o caminho conforme profundidade da pasta)
5. Nunca adicione CSS inline de estilo — use apenas classes do `styles.css` global ou um `<style>` no topo que use as variáveis definidas aqui

---

## Contexto do Projeto

- **Tipo:** POC (Proof of Concept) para demonstração a clientes
- **Modo:** Light mode apenas
- **Responsividade:** Desktop-first (1280px+), responsividade básica para tablets
- **Acessibilidade:** Labels semânticos, contraste adequado, foco visível nos inputs
