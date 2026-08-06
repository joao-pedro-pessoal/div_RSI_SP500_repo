# S&P 500 Trading Scanners

Infraestrutura para **5 scanners de trading**, com o Bot #1 implementado: divergências regulares entre preço e RSI nas ações do S&P 500.

O modo padrão replica a lógica do Pine v6 **"RSI Divergence Indicator"** fornecido pelo utilizador: os pivots são encontrados no RSI e o preço é comparado exatamente nas velas desses pivots.

O projeto **não executa ordens**. Analisa dados, deteta sinais por regras determinísticas e envia alertas para Telegram.

## O que já está implementado

- universo atual do S&P 500 obtido automaticamente;
- provider inicial gratuito com `yfinance`;
- download em batches + retry de símbolos falhados;
- OHLC ajustado (`auto_adjust: true`);
- RSI(14) com smoothing de Wilder e seed SMA;
- modo `tradingview` com pivots confirmados no próprio RSI;
- regular bullish divergence;
- regular bearish divergence;
- 1D, 3 sessões e 1W;
- apenas barras completas na análise;
- detector alternativo baseado em pivots do preço mantido para comparação;
- 8 grupos de validação defensiva de dados;
- bloqueio de descontinuidades com aspeto de split em dados supostamente ajustados;
- Telegram;
- IDs determinísticos e proteção contra alertas repetidos;
- heartbeat/resumo de cada scan;
- estado persistente;
- GitHub Actions automático;
- testes offline.

## Modo padrão: TradingView/Pine

O `config.yaml` vem agora com:

```yaml
detector_mode: tradingview
rsi_period: 14
pivot_left: 5
pivot_right: 5
min_distance_bars: 5
max_distance_bars: 60
```

Este modo segue a sequência do Pine fornecido:

```text
RSI(14)
  ↓
ta.pivotlow / ta.pivothigh no RSI (5 esquerda, 5 direita)
  ↓
comparar os dois pivots RSI consecutivos
  ↓
ler low/high do PREÇO exatamente nessas mesmas velas
  ↓
Regular Bull: RSI Higher Low + Price Lower Low
Regular Bear: RSI Lower High + Price Higher High
```

Os níveis RSI 30/70 **não são filtros** no Pine; servem apenas de linhas visuais. O Python segue isso.

Há ainda um detalhe de equivalência importante: o Pine usa `_inRange(plFound[1])`. Portanto, com `rangeLower=5` e `rangeUpper=60`, o `barssince` da série deslocada aceita 5..60, que corresponde a uma distância real de **6..61 barras entre os pivots**. O teste Python reproduz expressamente esse comportamento em vez de simplificar para 5..60.

### Momento real do alerta

Com `pivot_right: 5`, o pivot só é conhecido cinco barras mais tarde. O Pine desenha o label com `offset=-5`, em cima da vela do pivot, mas o alerta real só pode existir na vela de confirmação.

Isto significa aproximadamente:

- 1D → 5 barras diárias depois do pivot;
- 3D → 5 barras 3D depois;
- 1W → 5 semanas depois.

O scanner usa o momento de **confirmação**, não o local retroativo onde o TradingView desenha o label.

Referência TradingView sobre confirmação/offset de pivots: https://www.tradingview.com/pine-script-docs/faq/visuals/

## Detector alternativo para calibração

O detector anterior continua disponível. Para o ativar:

```yaml
detector_mode: price_pivots
```

Nesse detector, `rsi_alignment_mode: price_pivot` lê o RSI exatamente na vela onde o preço fez o pivot.

Alternativa:

```yaml
rsi_alignment_mode: rsi_pivot
rsi_pivot_window: 2
```

Com `rsi_alignment_mode: rsi_pivot`, o scanner procura um pivot independente do RSI perto do pivot do preço. Estes modos ficam disponíveis para investigação, mas **não são o default**.

## Pivots: defaults não são a estratégia final

No modo TradingView os defaults são:

```yaml
pivot_left: 5
pivot_right: 5
min_distance_bars: 5
max_distance_bars: 60
comparison_mode: consecutive
```

Isto significa que um pivot só fica confirmado depois de existirem 5 barras completas à sua direita. Em 1W, por exemplo, a confirmação é deliberadamente lenta.

No modo TradingView comparamos sempre pivots RSI consecutivos, como `ta.valuewhen(..., 1)` no Pine. `comparison_mode` aplica-se apenas ao detector alternativo.

## 3D

O `3D` está implementado como **três sessões de mercado completas**, usando o calendário do SPY como referência e uma âncora fixa (`2000-01-03`). Isto evita que IPOs, dados em falta ou uma janela de download diferente desloquem todas as barras futuras.

É uma definição explícita e reproduzível, mas **ainda deve ser comparada visualmente com o 3D exato que queres replicar no TradingView**. Se as fronteiras das velas forem diferentes, altera-se a transformação — não a lógica de divergência.

## Validação de dados

Antes de calcular RSI, cada série passa por verificações de:

1. colunas OHLC obrigatórias;
2. mínimo de observações;
3. timestamps ordenados e únicos;
4. NaN/inf;
5. preços positivos;
6. coerência Open/High/Low/Close;
7. gaps de calendário anormalmente longos;
8. saltos próximos de rácios típicos de split.

O provider pede dados ajustados. Por isso, se ainda aparecer uma descontinuidade quase exata de 2:1, 3:1, 4:1, etc., o ticker é bloqueado por defeito em vez de alimentar silenciosamente o RSI com dados possivelmente errados.

## Instalar e testar localmente

Requer Python 3.11+ (GitHub Actions usa 3.12).

```bash
python -m venv .venv
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Teste real pequeno, sem mandar Telegram:

```bash
python main.py --dry-run --tickers AAPL MSFT NVDA
```

Só 1D:

```bash
python main.py --dry-run --timeframe 1D --tickers AAPL MSFT
```

Sem `--tickers`, obtém automaticamente o universo do S&P 500 e analisa tudo.

## Telegram

O programa lê duas variáveis de ambiente:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Para desenvolvimento local podes copiá-las para o teu método preferido de gestão de ambiente. Nunca colocar os valores reais no repositório; `.env` já está ignorado.

No GitHub, cria dois repository secrets com exatamente esses nomes.

Formato aproximado de alerta:

```text
🟢 BULLISH RSI DIVERGENCE
AAPL — 1D

Low anterior (data): $...
Novo low (data): $... ↓

RSI anterior: ...
Novo RSI: ... ↑

Distância: ... candles
RSI alignment: price_pivot
Confirmado: ...
📊 TradingView
```

## Automação sem deixar o PC ligado

`.github/workflows/scan.yml` executa o scanner às **16:45 America/New_York, segunda a sexta**, e também permite `workflow_dispatch` manual.

O workflow:

1. obtém o projeto;
2. instala dependências;
3. executa todos os timeframes;
4. envia sinais e um heartbeat Telegram;
5. grava `state/signals.json` e `state/heartbeat.json` de volta no repositório.

O commit do estado resolve a persistência entre runners efémeros. Num repositório público, também cria atividade regular; a documentação do GitHub indica que schedules de repositórios públicos podem ser desativados após 60 dias sem atividade.

O workflow pede `contents: write`. Se o branch tiver uma regra que proíba pushes pelo `GITHUB_TOKEN`, é necessário ajustar a política do repositório ou escolher outro mecanismo de estado.

GitHub schedule docs: https://docs.github.com/actions/using-workflows/events-that-trigger-workflows

GitHub Actions billing: https://docs.github.com/billing/managing-billing-for-github-actions/about-billing-for-github-actions

## Heartbeat

Por defeito, cada execução termina com algo parecido com:

```text
✅ S&P 500 scanners concluídos
Ações: 500/503
Novos sinais: 2
Alertas enviados: 2
Falhas download: 3
Bloqueadas por dados: 0
Erros scan: 0
```

Assim, **0 sinais também produz confirmação de que o sistema correu**. Se o workflow nem sequer arrancar, não existe heartbeat; a ausência da mensagem é, portanto, observável.

## Custo

O scanner não usa um LLM em produção. O fluxo é:

```text
OHLC → validação → timeframe → RSI → pivots → divergência → Telegram
```

O provider inicial é `yfinance`, um projeto open source que utiliza APIs públicas do Yahoo Finance. A documentação do próprio projeto deixa claro que não é afiliado ao Yahoo e que os dados Yahoo se destinam a uso pessoal. O provider está isolado precisamente para poder ser trocado mais tarde.

yfinance docs: https://ranaroussi.github.io/yfinance/

## O que ainda NÃO considero validado

O software encontrar uma divergência não prova que exista edge de trading.

Antes de tratar os alertas como estratégia validada, faltam duas etapas diferentes:

1. **Equivalência de dados:** comparar sinais reais com o TradingView. A lógica Pine está reproduzida, mas Yahoo/yfinance e TradingView podem ter diferenças de OHLC/ajustes e a agregação 3D ainda precisa de comparação direta.
2. **Edge:** backtest fora da amostra, sem survivorship bias e medindo o resultado a partir do momento em que o sinal era realmente conhecível (depois da confirmação do pivot), não a partir do pivot retroativamente.

Também falta validar empiricamente o comportamento do `yfinance` nos runners do GitHub durante alguns dias. Se houver bloqueios/rate limits frequentes, troca-se apenas o provider.

Para um backtest sério de 10+ anos, o universo histórico deve ser **point-in-time**; usar os constituintes atuais do S&P 500 para anos antigos introduz survivorship bias.

## Próximo passo de calibração

Escolhe 5–10 exemplos reais do próprio indicador:

- 3–5 divergências que o bot **tem obrigatoriamente de encontrar**;
- 2–3 situações visualmente parecidas que **não queres receber**;
- idealmente exemplos 1D, 3D e 1W.

Com esses exemplos podemos confirmar primeiro se `tradingview` em Python encontra exatamente os mesmos sinais nas mesmas datas. Só depois faz sentido alterar 5/5, distância ou outros thresholds e medir se há edge.
