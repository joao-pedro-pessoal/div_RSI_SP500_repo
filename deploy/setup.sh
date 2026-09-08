#!/usr/bin/env bash
# =============================================================================
# setup.sh — instala os scanners numa VM Ubuntu (Oracle Cloud Always Free)
#
# Corre UMA VEZ, com o utilizador normal (nao root):
#     bash setup.sh https://github.com/<utilizador>/<repo>.git
#
# O que faz:
#   - instala python e git
#   - clona o repositorio para ~/scanners
#   - cria um ambiente virtual e instala as dependencias
#   - prepara o ficheiro de segredos com permissoes restritas
#   - NAO instala o cron: isso e um passo separado e deliberado, para
#     poderes testar antes de deixar a correr sozinho
# =============================================================================

set -euo pipefail

REPO_URL="${1:-}"
DEST="${HOME}/scanners"

if [[ -z "$REPO_URL" ]]; then
    echo "uso: bash setup.sh <url-do-repositorio-git>"
    exit 1
fi

# Swap. Medido: o pico de memoria e ~350 MB de 1 GB, portanto NAO e
# necessario -- e seguro barato para o caso de o universo crescer ou de
# uma versao futura carregar mais historico. Custa 1 GB de disco dos 30.
echo "==> swap"
if ! swapon --show | grep -q swapfile; then
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    echo "    1 GB de swap criado"
else
    echo "    swap ja existe"
fi

echo "==> pacotes do sistema"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git

echo "==> repositorio"
if [[ -d "$DEST/.git" ]]; then
    git -C "$DEST" pull --ff-only
else
    git clone --depth 50 "$REPO_URL" "$DEST"
fi

echo "==> ambiente virtual"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet -r "$DEST/requirements.txt"

echo "==> ficheiro de segredos"
ENV_FILE="$DEST/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<'ENVEOF'
# Preenche estes valores. NAO commitar este ficheiro.
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_TOPIC_ID_CRYPTO=
TELEGRAM_TOPIC_ID_SWEEP=
TELEGRAM_TOPIC_ID_SP500=
ENVEOF
    echo "    criado $ENV_FILE — preenche-o antes de continuar"
else
    echo "    $ENV_FILE ja existe, mantido"
fi
# Legivel apenas pelo dono: o token do Telegram da controlo total do bot.
chmod 600 "$ENV_FILE"

mkdir -p "$DEST/logs"

echo
echo "==> instalado em $DEST"
echo
echo "Passos seguintes:"
echo "  1. nano $DEST/.env          (preenche os quatro valores)"
echo "  2. bash $DEST/deploy/run.sh sweep config_sweep_1h.yaml --dry-run"
echo "  3. bash $DEST/deploy/install_cron.sh"
