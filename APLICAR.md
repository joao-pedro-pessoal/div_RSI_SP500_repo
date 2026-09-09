# Correcao do 429 da CoinGecko

## O que estava mal

Duas coisas.

1. O RETRY NUNCA FUNCIONOU. O _get_json convertia o HTTPError num
   RuntimeError para incluir o host na mensagem, mas o _get_json_retry
   apanhava HTTPError -- que ja nao chegava la. Um 429 subia direto e
   derrubava o scan inteiro. Bug meu, presente desde o inicio.

2. HORARIOS SOBREPOSTOS. O scanner de 1h e o de 4h arrancavam ambos aos
   :02 e pediam o ranking a CoinGecko ao mesmo tempo. O escalao gratuito
   nao aguenta.

## O que muda

- O retry funciona: 15s, 30s, 60s, 120s de espera entre tentativas
- Cache do ranking em universe/last_ranking.json, valida 72h.
  Se a CoinGecko falhar de todo, usa-se o ranking de ontem em vez de nao
  correr. O top 100 por market cap muda devagar.
- Horarios espacados: 1h aos :02, 4h aos :08, diarios aos :14 e :26
- Todas as linhas do cron passam a chamar `bash` explicitamente, para
  nao dependerem da permissao de execucao (que se perde a cada git pull
  vindo do Windows)

## Aplicar no PC

    cd C:\Users\joao2\Downloads\scanner_pronto_para_github\pronto
    tar -xf fix_coingecko_429.zip
    git status                (confirma "On branch main")
    git add -A
    git commit -m "corrige retry do 429 e espaca horarios"
    git pull --rebase
    git push

## Aplicar na VM

    cd ~/scanners
    git checkout . && git pull
    chmod +x deploy/*.sh
    bash deploy/install_cron.sh
    crontab -l

## Verificar

    bash deploy/run.sh crypto config_crypto_4h.yaml --dry-run
    tail -n 20 logs/crypto_config_crypto_4h_$(date -u +%Y%m%d).log

Se aparecer "a usar ranking em cache", a CoinGecko esta a limitar mas o
scan corre na mesma -- que e o objetivo.
