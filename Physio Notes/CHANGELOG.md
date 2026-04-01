# Changelog — Physio Notes

Todas as mudanças relevantes por versão. Usado como corpo do commit/tag de release.

---

## Beta-0.14 — 2026-04-01

### Funcionalidades
- **Multi-tenancy**: cada usuário vê apenas seus próprios pacientes — isolamento total de dados
- **Painel de administração** (`/admin.html`): admin aprova ou revoga acesso de usuários
- **Controle de acesso**: novos usuários ficam pendentes até aprovação do admin; mensagem clara na tela de login
- **Saudação neutra** no login: "Olá, [nome]!" em vez de "Bem-vinda"

### Melhorias
- Login simplificado: apenas Google SSO (biometria removida para evitar acesso não rastreado)
- Token JWT reduzido de 72h para 8h (melhor segurança para dados clínicos)
- Link "Gerenciar usuários" no drawer visível apenas para o admin
- `ADMIN_EMAIL` configurável via `.env`

### Correções
- Envio de áudio retornava 401: chamadas ao `/transcrever` não enviavam o token de autenticação
- Conflito de schema entre tabela `usuario` (WebAuthn) e `usuario_google` (SSO)
- Pacote `requests` adicionado ao `requirements.txt` (necessário para verificação do token Google)

---

## Beta-0.12 — 2026-04-01

### Melhorias
- Agente IA reclassificado como fisioterapeuta clínico experiente em todos os prompts — melhora a interpretação de jargões, abreviações (TENS, FNP, RPG, ADM, EVA) e a qualidade das notas de sessão, anamnese e respostas clínicas

---

## Beta-0.11 — 2026-04-01

### Melhorias
- Labels "Competência" e "Paciente" centralizados e em negrito (Faturamento + Notas Fiscais)
- Versão do app exibida no footer do drawer para controle do testador

---

## Beta-0.10 — 2026-04-01

### Funcionalidades
- CPF (com validação de dígitos verificadores) e endereço obrigatórios no cadastro de paciente
- CPF e endereço extraídos automaticamente por voz; feedback informa o que falta
- Busca de pacientes por nome, CPF, data de nascimento ou endereço (campo livre)
- Anamnese: card recolhido por padrão com preview de 80 chars
- Voz como ação primária na anamnese; edição manual como secundária
- Complemento de anamnese por voz — IA integra nova transcrição ao texto existente em linguagem clínica
- Anamnese renderizada com markdown (títulos, negrito, listas)
- Faturamento agrupado por paciente + mês com checkboxes para emissão de NFS-e individual
- Notas Fiscais: filtro por competência (grade de mês/ano) e por paciente
- Exclusão de paciente exige digitar "EXCLUIR" para confirmar
- Timer de auto-encerramento de sessão: 5 minutos
- Billing mobile: layout responsivo, cards centralizados, badge "Mês atual" corrigido
- Pacotes sem data_pagamento usam data de criação no filtro de competência
