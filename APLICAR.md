# Separacao do bot de divergencias cripto

## Aplicar

    tar -xf crypto_divergence_split.zip

## APAGAR os antigos (substituidos)

    del config_crypto.yaml
    del .github\workflows\scan_crypto.yml
    del state\crypto_signals.json
    del state\crypto_heartbeat.json

Se ficarem, tens um workflow duplicado a correr 1x/dia com todos os
timeframes, alem dos dois novos.

## Confirmar e enviar

    python -m unittest discover -s tests -q     (deve dar 81)
    git add -A
    git commit -m "separa divergencias cripto por timeframe"
    git pull --rebase
    git push

## Workflows resultantes

| Workflow                  | Frequencia          | Paginas/moeda |
|---------------------------|---------------------|---------------|
| Crypto Divergence 4h      | 0,4,8,12,16,20 UTC  | 4             |
| Crypto Divergence Daily   | 1x/dia, 00:20 UTC   | 42            |

Ambos usam TELEGRAM_TOPIC_ID_CRYPTO e escrevem no mesmo topico.
Nao ha secrets novos.

## Estado final: 6 workflows

| Workflow                | Frequencia         | Exec/mes |
|-------------------------|--------------------|----------|
| S&P 500                 | 16:45 ET, seg-sex  | 30       |
| Crypto Divergence 4h    | 6x/dia             | 180      |
| Crypto Divergence Daily | 1x/dia             | 30       |
| Sweep 1h                | 24x/dia            | 720      |
| Sweep 4h                | 6x/dia             | 180      |
| Sweep Daily             | 1x/dia             | 30       |
