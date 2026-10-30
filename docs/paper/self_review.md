# Autocrítica do manuscrito, no papel de revisor (uso interno, não submeter)

Manuscrito: `docs/paper/manuscript.md`. Esta nota lista o que um revisor exigente de periódico de finanças quantitativas apontaria, o que já está tratado no texto, e o que falta fazer antes de submeter.

## Recomendação provável de um revisor, hoje
**Major revision** em periódico de finanças quantitativas (Quantitative Finance, Journal of Financial Data Science). O tema (auditoria de um catálogo inteiro, paridade simulador-executor, rótulo explícito para estratégias não testáveis) é útil e pouco coberto, e os resultados negativos são relatados com honestidade. As fraquezas são de inferência estatística e de generalidade, e todas são corrigíveis com análises adicionais.

## Preocupações maiores (precisam de trabalho novo)

1. **Sem correção formal para múltiplos testes.** A seção 5.6 faz só uma conta de plausibilidade (0,05 x 144 = 7,2 positivos esperados contra 5 documentados). Um revisor vai exigir White (2000) / Hansen (2005) / Romano-Wolf, ou Deflated Sharpe e Probability of Backtest Overfitting (Bailey et al.). *Fazer:* exportar os P&L por trade de todas as 48 estratégias testáveis nos 3 ativos e calcular um reality check com bootstrap estacionário sobre o universo completo.
2. **Contagem de positivos incompleta.** Não preservei o baseline completo das demais estratégias 1D aposentadas no regime final (sem teto de 5%). *Fazer:* rodar `baseline --ids TF --tfs 1D` no regime atual e fechar a tabela de 144 pares.
3. **Bootstrap i.i.d.** Os trades se agrupam por regime. *Fazer:* repetir o PF p5 com bootstrap estacionário ou por blocos (Politis e Romano, 1994) e mostrar quais vereditos mudam.
4. **Contaminação nos números dos candidatos.** Os PF de TF008 e RG005 usam parâmetros escolhidos no mesmo histórico (o texto avisa). *Fazer:* reportar o desempenho só da janela de teste do walk-forward como resultado principal, ou separar um período final nunca usado (holdout) e avaliar os três candidatos nele uma única vez. É a única forma de transformar "candidata" em evidência.
5. **Veredito por julgamento humano.** Os critérios são escritos, não pré-registrados. *Fazer:* formalizar as regras em código, aplicar de forma cega a todas as estratégias e relatar a concordância com os vereditos manuais.
6. **Paridade só parcialmente medida.** Foi quantificada uma das quatro diferenças (teto do stop). As outras três (breakeven, ativação do trailing, teto de callback) foram corrigidas na execução sem medir o efeito. *Fazer:* adicionar ao simulador um modo "estilo antigo da execução" e medir o delta de PF dos candidatos; isso fortalece a tese central do artigo.
7. **Sem benchmark ajustado a risco.** Só PF, lucro em USD com stake fixo e comparação em retorno bruto. *Fazer:* Sharpe/Sortino em série temporal diária, drawdown e comparação com buy-and-hold ajustado à mesma volatilidade; testar também com capital composto.

## Preocupações médias
- **Generalidade:** uma corretora, três ativos sobreviventes, SOL com 5,8 anos, um único mercado de baixa na janela diária. Ampliar com mais pares e outra corretora, ou declarar como estudo de caso.
- **Custos:** slippage constante de 0,05%. Fazer análise de sensibilidade (0,02%, 0,05%, 0,10%) e com taxa de maker (a conclusão intraday deve ficar mais forte, mas precisa ser mostrada).
- **Fidelidade das estratégias:** o veredito vale para a implementação, não para a ideia original. Dizer qual fração foi auditada contra a fonte publicada.
- **Evidência ao vivo:** só 10 trades em Demo, com regras de risco que foram depois corrigidas. Não usar como evidência. Se possível, adicionar um teste futuro (forward) pré-registrado dos três candidatos, com data de início e critério de parada.
- **Qualidade de dados:** descrever tratamento de lacunas e do backfill idempotente, e checar duplicatas e candles ausentes por série.

## Preocupações menores
- Falta figura: curva de PF em função de trades por ano (dispersão com o Spearman de -0,37) e as barras de custo por round trip.
- Padronizar casas decimais (PF com 2 casas) e unidades (USD sobre stake de USD 100).
- O texto usa "candidate/watch/retired" sem definição quantitativa dos limiares de "material contradiction". Tornar numérico.
- A seção 5.2 mistura 1H, 15m e 5m. Separar por timeframe permite ver se o efeito do custo depende do número de barras ou do número de trades.
- Seção de conflito de interesses tem um ponto a confirmar (se o autor opera a plataforma).

## Verificação das referências
Conferidas por busca (autores, periódico, volume, páginas ou DOI):
- Gerritsen, Bouri, Ramezanifar e Roubaud (2020), *Finance Research Letters* 34, 101263. Confirmado.
- Hudson e Urquhart (2021), *Annals of Operations Research* 297, 191-220. Confirmado.
- Corbet, Eraslan, Lucey e Sensoy (2019), *Finance Research Letters* 31, 32-37. Confirmado.
- Bailey, Borwein, López de Prado e Zhu (2017), *Journal of Computational Finance* 20(4). Confirmado; **faltam as páginas** (conferir no DOI 10.21314/JCF.2016.322) e o ano varia entre bases (2016/2017).

**Citadas de memória, a conferir uma a uma antes de submeter** (volume, páginas, ano): Bailey et al. (2014, Notices AMS 61(5)); Bailey e López de Prado (2014, JPM 40(5)); Benjamini e Hochberg (1995); Brock, Lakonishok e LeBaron (1992); Efron (1979); Hansen (2005); Harvey (2017); Harvey e Liu (2015); Harvey, Liu e Zhu (2016); Liu e Tsyvinski (2021); Lo e MacKinlay (1990); López de Prado (2018); Pardo (2008); Park e Irwin (2007); Politis e Romano (1994); Sullivan, Timmermann e White (1999); White (2000).

Ainda faltam, para um artigo completo: revisão de literatura sobre custos de transação e market impact em cripto, e sobre a microestrutura de taxas (maker/taker) da corretora.

## O que no texto veio de medição ao vivo e não está no repositório
- As medições de 1D de TF008, RG005 e TF014/TF017 sob os três regimes de teto (tabela 3) foram impressas no terminal da VPS e coladas na conversa. Antes de submeter, regravar essas saídas em `sources/` e reproduzi-las com `OKX_MAX_SL_PCT=0.05|0.10|0`.
- O diagnóstico de RG004 em dados sintéticos (0 de 600 janelas) veio de um script temporário. Mover para teste ou script versionado.
- As contagens por família e os números de baseline intraday vêm de `sources/baseline.txt` e dos relatórios gerados; essas são reprodutíveis.

## Periódicos-alvo (em ordem de aderência)
1. **The Journal of Financial Data Science**: aceita estudos aplicados de dados e implementação, tolera estudo de caso.
2. **Quantitative Finance**: exige inferência mais forte (itens 1 a 4 acima são obrigatórios).
3. **Digital Finance** ou **Finance Research Letters** (versão curta, só a tese de paridade simulador-executor e o resultado de custos).
4. **PLOS ONE**: aceita resultados negativos e dá peso à reprodutibilidade, com exigência de disponibilizar código e dados. Exigiria publicar o pacote de reprodução e uma amostra dos candles.

## Ordem sugerida para o próximo ciclo de trabalho
Itens 2, 1 e 4 (dados completos, reality check, holdout) são os que mais mudam a credibilidade. O item 6 (medir as outras três divergências) é o de maior valor científico e o mais barato: uma flag no simulador e uma rodada de baseline.
