# Página Admin: Revisar, Otimizar, Compilar e Publicar algoritmos

Status: **proposta (análise de viabilidade), nada implementado.**

## Ideia
Uma página Admin com botões para Revisar, Otimizar e Compilar os algoritmos produzidos pela Fábrica,
atualizando o GitHub e fazendo o deploy na VPS, protegidos por uma "senha dinâmica".

## Veredito
Viável, com três ressalvas que mudam o desenho.

## 1. Senha dinâmica
- Um código exibido na tela **não é um segredo**: quem vê a tela (ou chama a API que o gera) já o conhece.
  Protege contra clique acidental, não contra acesso indevido.
- Uso correto: **confirmação de operação** (o usuário digita o hash curto do commit ou o resumo do diff mostrado).
- Autenticação real precisa de camada própria: login de admin com senha forte e/ou **TOTP** (a versão correta de
  "senha dinâmica").

## 2. Segurança (ponto crítico)
Para fazer git push e deploy, o container da app precisaria de token de escrita no GitHub e acesso ao Docker/SSH
do host. Comprometer a API/página Admin significaria controlar o servidor.

Arquitetura mais segura:
- A app só grava o código revisado num branch, com deploy key restrita a um repositório (ou PAT de escopo mínimo).
- O **deploy fica fora do container**: GitHub Actions disparado pelo push, ou agente no host que só executa
  `deploy.sh`. O container nunca recebe acesso ao Docker.

## 3. O que cada botão significa
- **Revisar**: automatizável e sem risco (análise estática, política de código, testes de contrato, baseline,
  relatório).
- **Compilar**: Python não compila; na prática é validar (`py_compile`, política, testes, carga no registry).
- **Otimizar**: parte perigosa. Walk-forward leva 30 a 40 min, então exige fila de jobs em segundo plano com
  status e log; o deploy reinicia o container e mata esses jobs, então não podem rodar juntos. Aplicar parâmetros
  automaticamente gera overfitting (caso TF009): o botão deve **propor** mudanças com os critérios de aceite
  (planalto, convergência nos 3 ativos, melhora fora da amostra) e a decisão final é humana.

## 4. Código gerado por IA (Fábrica)
- Testes e política devem passar; falha bloqueia o deploy.
- A estratégia entra desativada e só em Demo.
- Commit identificável e reversível.
- Regra do projeto: o git é a fonte única. O botão grava no git, não no banco.

## Plano em etapas
1. **Admin somente leitura + jobs**: Revisar, Validar e Otimizar em segundo plano, com relatório. Sem push nem
   deploy. Baixo risco, útil de imediato.
2. **Publicar**: botão que grava no git após os testes passarem, com confirmação (hash do commit) e login de admin
   com TOTP.
3. **Deploy automático** via GitHub Actions ou agente no host, nunca dentro do container.

## Perguntas em aberto
- Qual é exatamente o "código do deploy" que aparece hoje na tela (hash do commit)?
- O acesso à app é só do dono ou há outros usuários? Define se basta um login de admin simples.
