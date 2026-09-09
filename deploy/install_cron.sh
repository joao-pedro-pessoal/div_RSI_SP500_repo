#!/usr/bin/env bash
# =============================================================================
# install_cron.sh — instala os agendamentos.
#
# O cron do sistema nao descarta execucoes. Foi essa a razao de sair do
# GitHub Actions: la, cerca de 44% das execucoes agendadas nao corriam, e
# com uma janela de alerta de 1 barra cada falha perdia esse sinal.
#
# Aqui basta UM gatilho por periodo. Os tres gatilhos redundantes e o
# --skip-if-recent deixam de fazer falta.
# =============================================================================

set -euo pipefail

DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$DEST/deploy/run.sh"

if [[ ! -f "$DEST/.env" ]]; then
    echo "erro: falta $DEST/.env" >&2
    exit 1
fi
if ! grep -q '^TELEGRAM_BOT_TOKEN=.\+' "$DEST/.env"; then
    echo "erro: TELEGRAM_BOT_TOKEN vazio em $DEST/.env" >&2
    exit 1
fi

MARK_START="# >>> scanners inicio"
MARK_END="# <<< scanners fim"

BLOCK=$(cat <<EOF
$MARK_START
# Horarios em UTC. A VM deve estar em UTC -- ver o guia.
#
# Minuto :02 e nao :00: a vela fecha aos :00 e a OKX precisa de um momento
# para a marcar como fechada (confirm=1). Um minuto e apertado.
CRON_TZ=UTC

# HORARIOS ESPACADOS.
#
# O escalao gratuito da CoinGecko permite poucas chamadas por minuto, e
# TODOS os scanners pedem o ranking de market cap. Quando o de 1h e o de
# 4h arrancavam ambos aos :02, o segundo apanhava 429 e falhava.
#
# Ha tambem cache do ranking (universe/last_ranking.json) como rede de
# seguranca, mas espacar evita o problema em vez de o remediar.

# Varrimentos
2 * * * *              bash $RUN sweep  config_sweep_1h.yaml
8 0,4,8,12,16,20 * * * bash $RUN sweep  config_sweep_4h.yaml
14 0 * * *             bash $RUN sweep  config_sweep_daily.yaml

# Divergencias de RSI (cripto)
20 0,4,8,12,16,20 * * * bash $RUN crypto config_crypto_4h.yaml
26 0 * * *             bash $RUN crypto config_crypto_daily.yaml

# S&P 500 — segunda a sexta.
#
# 21:45 UTC e nao 20:45: no verao americano sao 17:45 ET, no inverno 16:45
# ET. O mercado fecha as 16:00 ET nos dois casos, portanto uma hora fixa em
# UTC funciona o ano todo sem depender de suporte a CRON_TZ, que varia
# entre implementacoes de cron.
45 21 * * 1-5          bash $RUN sp500 config.yaml

# Atualizar o codigo todos os dias as 4h UTC, para as correcoes que fizeres
# no GitHub chegarem a VM sem teres de entrar nela.
30 4 * * *             cd $DEST && git pull --ff-only >> $DEST/logs/update.log 2>&1
$MARK_END
EOF
)

# Substituir apenas o nosso bloco, preservando outras entradas do utilizador.
CURRENT="$(crontab -l 2>/dev/null || true)"
CLEANED="$(printf '%s\n' "$CURRENT" | sed "/^${MARK_START}$/,/^${MARK_END}$/d")"
printf '%s\n%s\n' "$CLEANED" "$BLOCK" | sed '/^$/N;/^\n$/D' | crontab -

echo "==> cron instalado:"
crontab -l | sed -n "/^${MARK_START}$/,/^${MARK_END}$/p"
echo
echo "Hora da VM: $(date -u +'%Y-%m-%d %H:%M:%S') UTC"
echo "Se nao estiver em UTC:  sudo timedatectl set-timezone UTC"
