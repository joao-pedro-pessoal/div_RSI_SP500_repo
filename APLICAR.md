# Estado completo — substitui TODOS os zips anteriores de cripto

Este zip tem o estado final de tudo. A ordem de aplicacao deixa de
importar: nao ha risco de um zip antigo sobrepor alteracoes novas.

## Aplicar

    cd C:\Users\joao2\Downloads\scanner_pronto_para_github\pronto
    tar -xf estado_completo_cripto.zip

## Verificar ANTES de commitar

    python -c "import sys; sys.path.insert(0,'.'); from crypto_scanner.sweep import SweepParams; print(SweepParams().min_wick_fraction)"

Tem de imprimir 0.45. Se imprimir outro valor, o tar nao sobrepos.

    python -m unittest discover -s tests -q      -> 89 testes, OK
    git status                                    -> "On branch main"

## Enviar

    git add -A
    git commit -m "estado final dos scanners de cripto"
    git pull --rebase
    git push

## APAGAR se ainda existirem (substituidos)

    del config_sweep.yaml
    del config_crypto.yaml
    del .github\workflows\scan_sweep.yml
    del .github\workflows\scan_crypto.yml
    del state\sweep_signals.json state\sweep_heartbeat.json
    del state\crypto_signals.json state\crypto_heartbeat.json

## Se apanhares conflito no git pull --rebase

Resolve, faz "git add", e depois **git rebase --continue** -- NAO git commit.
Um commit a meio de um rebase deixa-te em detached HEAD.

## Secrets necessarios

    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    TELEGRAM_TOPIC_ID          (S&P 500, topico 2)
    TELEGRAM_TOPIC_ID_CRYPTO   (divergencias, topico 778)
    TELEGRAM_TOPIC_ID_SWEEP    (varrimentos, topico novo)
