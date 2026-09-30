# Links Bybit e destaque por capitalização

Esta versão mantém os dados e sinais calculados com velas da OKX. Os links
TradingView passam a abrir o contrato perpétuo USDT da Bybit com o mesmo
símbolo base (`BYBIT:BTCUSDT.P`, por exemplo). Não são feitos pedidos à API
Bybit: esta versão é compatível com a VM atual nos EUA.

As velas e os indicadores das duas bolsas podem diferir. As mensagens
identificam a origem: `Dados: OKX · Gráfico: Bybit`. O link não verifica a
existência do contrato na Bybit; algumas moedas podem ter um nome diferente
ou não estar disponíveis nessa bolsa.

## Destaques em sweeps e divergências

| Grupo | Etiqueta | Envio |
|---|---|---|
| BTC | 🟠 ₿ BITCOIN — BTC | Individual, antes de todos |
| Restantes posições 1–10 | 🥇 TOP 10 · #posição | Individual, depois do BTC |
| Posições 11–50 | 🔷 TOP 50 · #posição | Grupos próprios até 10 sinais; individual nos timeframes altos |
| Posições 51+ / ranking desconhecido | Sem destaque de ranking | Grupos próprios; individual nos timeframes altos |

A posição é `market_cap_rank` global da CoinGecko, antes de excluir stablecoins,
wrapped tokens ou pares sem perpétuo OKX. O campo antigo `Coin.rank` continua
a indicar a posição dentro do universo filtrado; não é usado nos destaques.

O ranking é o obtido no scan (ou o ranking em cache já permitido pelo projeto,
com até 72 horas). Não se acrescentam chamadas de rede. Com `--symbols`, o
universo é ignorado: o BTC continua destacado, mas as outras moedas ficam sem
categoria por não se ter consultado o ranking. Não se inventam posições.

Aplica-se às divergências normais e antecipadas e aos sweeps de todos os
horários. Os destaques de timeframe alto mantêm-se. As regras de deteção e os
ficheiros de deduplicação são preservados. O envio pára na primeira falha,
marcando como enviados apenas os sinais entregues antes dela.

## Atualizar na VM atual

Depois de esta versão estar em `main`, aguardar o fim de qualquer scan em curso:

```bash
cd ~/scanners
pgrep -af '[p]ython.*main(_crypto|_sweep|_comp)?\.py'
git status --short
git pull --ff-only origin main
```

Se o `pgrep` listar um scan, esperar que termine antes do pull. Se o pull
acusar alterações locais, preservá-las e resolver o conflito; não apagar os
estados nem o `.env`. Não há dependências novas nem alterações de cron.

Teste só de leitura de mercado, sem enviar mensagens nem gravar estado/heartbeat:

```bash
bash deploy/run.sh crypto config_crypto_4h.yaml --dry-run --symbols BTC-USDT-SWAP
bash deploy/run.sh sweep config_sweep_1h.yaml --dry-run --symbols BTC-USDT-SWAP
tail -n 40 logs/crypto_config_crypto_4h_$(date -u +%Y%m%d).log
tail -n 40 logs/sweep_config_sweep_1h_$(date -u +%Y%m%d).log
```

Zero sinais é normal se não houver um padrão recente; os testes abaixo
verificam também mensagens artificiais e todas as fronteiras do ranking.
O cron já instalado usará o código novo na execução seguinte.

```bash
python -m unittest tests.test_crypto_scanner tests.test_rank_alerts -v
```

Não juntar a antiga branch `codex/bybit-btc-alerts`: essa troca o fornecedor
de dados e continua sujeita ao bloqueio da API Bybit na VM dos EUA.
