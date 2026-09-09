#!/usr/bin/env bash
# =============================================================================
# run.sh — corre um scanner. E isto que o cron chama.
#
#     bash run.sh sweep  config_sweep_1h.yaml
#     bash run.sh crypto config_crypto_4h.yaml
#     bash run.sh sweep  config_sweep_1h.yaml --dry-run
#
# PORQUE UM WRAPPER E NAO O CRON A CHAMAR O PYTHON DIRETAMENTE
#   O cron corre com um ambiente minimo: sem PATH util, sem variaveis, e a
#   partir de uma pasta que nao e a do projeto. Um comando que funciona no
#   terminal falha no cron por essas tres razoes. O wrapper resolve-as num
#   sitio so.
#
#   Alem disso, o cron engole o stdout. Sem registo em ficheiro, uma falha
#   as 4 da manha nao deixa rasto nenhum.
# =============================================================================

set -uo pipefail

KIND="${1:?uso: run.sh <sweep|crypto> <config.yaml> [opcoes]}"
CONFIG="${2:?falta o ficheiro de configuracao}"
shift 2

DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DEST"

# Segredos. O `set -a` exporta tudo o que for definido a seguir.
if [[ -f "$DEST/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$DEST/.env"
    set +a
fi

# Cada scanner escreve no seu topico. O codigo le TELEGRAM_TOPIC_ID; o
# valor vem de uma variavel diferente conforme o bot -- a mesma solucao
# que se usava nos workflows do GitHub.
case "$KIND" in
    sweep)  export TELEGRAM_TOPIC_ID="${TELEGRAM_TOPIC_ID_SWEEP:-}"  ; MAIN="main_sweep.py"  ;;
    crypto) export TELEGRAM_TOPIC_ID="${TELEGRAM_TOPIC_ID_CRYPTO:-}" ; MAIN="main_crypto.py" ;;
    sp500)  export TELEGRAM_TOPIC_ID="${TELEGRAM_TOPIC_ID_SP500:-}"  ; MAIN="main.py"        ;;
    *) echo "tipo desconhecido: $KIND (usa sweep, crypto ou sp500)" >&2; exit 2 ;;
esac

# Sem isto o Python guarda a saida em memoria quando escreve para ficheiro
# em vez do terminal, e so a despeja ao terminar. O log parece parado
# durante minutos e nao ha forma de saber se o scan progride ou encravou.
export PYTHONUNBUFFERED=1

STAMP="$(date -u +%Y%m%d)"
LOG="$DEST/logs/${KIND}_$(basename "$CONFIG" .yaml)_${STAMP}.log"
mkdir -p "$DEST/logs"

{
    echo "===== $(date -u +'%Y-%m-%d %H:%M:%S') UTC | $MAIN --config $CONFIG $* ====="
    "$DEST/.venv/bin/python" "$DEST/$MAIN" --config "$CONFIG" "$@"
    CODE=$?
    echo "----- saida: $CODE -----"
    exit $CODE
} >> "$LOG" 2>&1

CODE=$?

# Limpar logs com mais de 14 dias. A VM gratuita tem disco limitado e um
# scan de hora a hora enche-o sem isto.
find "$DEST/logs" -name '*.log' -mtime +14 -delete 2>/dev/null || true

exit $CODE
