# Atualizar os scanners para Bybit + destaque BTC

Esta versão usa dados públicos de contratos perpétuos USDT da Bybit (API V5,
`category=linear`), sem chave de API. Não usa spot nem mark-price candles.
O ranking por capitalização continua a vir da CoinGecko; só entram pares
exatos SYMBOLUSDT disponíveis na Bybit. Contratos com nomes diferentes do
ticker CoinGecko podem ficar de fora. Não existe fallback automático para OKX.

BTC passa a ter mensagem individual, faixa «₿ BITCOIN — BTC» e prioridade
sobre os outros sinais, tanto em liquidity sweeps como em divergências,
incluindo as antecipadas. As regras de deteção não mudam. Os links abrem
BYBIT:BTCUSDT.P. O COMP também recebe os dados Bybit, pois partilha o provider.
O S&P 500 mantém o seu provider.

## 1. Testar na VM ANTES de atualizar a main

O guia GCP antigo recomendava `us-central1`. A Bybit documenta bloqueio de
IPs nos EUA com HTTP 403. Google Cloud, por si só, não resolve esse bloqueio.
Não foi possível verificar a tua VM a partir deste ambiente.

Abre Compute Engine → VM instances → SSH na máquina onde já corre o bot.
Cloud Shell sozinho NÃO é o terminal da VM. Se preferires Cloud Shell:

```bash
gcloud compute instances list
gcloud compute ssh NOME_DA_VM --zone ZONA_DA_VM --project ID_DO_PROJETO
```

Dentro da VM (o caminho `~/scanners` é o usado no guia do projeto):

```bash
cd ~/scanners
git fetch origin
BYBIT_CHECK_DIR=$(mktemp -d)
git show origin/codex/bybit-btc-alerts:crypto_scanner/provider.py > "$BYBIT_CHECK_DIR/provider.py"
git show origin/codex/bybit-btc-alerts:check_bybit.py | sed 's/from crypto_scanner.provider import/from provider import/' > "$BYBIT_CHECK_DIR/check_bybit.py"
.venv/bin/python "$BYBIT_CHECK_DIR/check_bybit.py"
```

Só continuar se aparecer **BYBIT PREFLIGHT OK**. Este teste usa apenas dados
públicos: não envia Telegram nem altera estados, cron ou código em produção.
Valida instrumentos, BTC e mais de uma página de velas de 4h.

Se der 403: mantém a versão atual. Confirma a disponibilidade regional com a
Bybit e usa alojamento numa região onde o acesso seja permitido, ou mantém a
OKX. Não basta mudar símbolos ou adicionar uma API key. Uma mudança de região
no GCP pode implicar custos; não assumas as condições gratuitas do guia antigo.

Documentação: https://bybit-exchange.github.io/docs/v5/guide

## 2. Atualizar o GitHub (depois do teste OK)

No terminal de uma cópia local do repositório com Git e permissões de escrita:

```bash
git status --short
git switch main
git pull --ff-only origin main
git fetch origin
git merge --no-ff origin/codex/bybit-btc-alerts -m "Use Bybit crypto data and highlight Bitcoin alerts"
git push origin main
```

Se `git status` mostrar alterações tuas, guarda-as antes de trocar de branch.
Não uses reset/clean para descartar dados. Se o merge tiver conflitos, resolve
os ficheiros indicados antes do commit/push.

**Atenção:** o cron existente atualiza a `main` às 04:30 UTC. Publicar em `main`
pode ativar esta versão automaticamente na próxima atualização diária.

## 3. Atualizar e verificar a VM

Executa fora de um scan em curso. O cron usa processos Python novos em cada
execução; não existe serviço do bot para reiniciar.

```bash
cd ~/scanners
pgrep -af '[p]ython.*main(_crypto|_sweep|_comp)?\.py'
git status --short
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python check_bybit.py
```

Se o primeiro comando listar scanners ativos, espera que terminem antes do
pull. Se o pull acusar alterações locais, preserva-as e resolve o conflito;
não sobrescrevas `.env`, `state/` ou `universe/`. Os estados de OKX têm símbolos
diferentes: os primeiros scans Bybit podem voltar a alertar sinais recentes.

Testar sem enviar mensagens (estes dry runs não escrevem heartbeat/estado):

```bash
bash deploy/run.sh crypto config_crypto_4h.yaml --dry-run --symbols BTCUSDT ETHUSDT
bash deploy/run.sh sweep config_sweep_1h.yaml --dry-run --symbols BTCUSDT ETHUSDT
tail -n 60 logs/crypto_config_crypto_4h_$(date -u +%Y%m%d).log
tail -n 60 logs/sweep_config_sweep_1h_$(date -u +%Y%m%d).log
```

Deve aparecer `saida: 0`, downloads completos e zero erros de scan. Zero
sinais pode ser normal: o bot só avisa quando deteta os padrões.

O cron e os tópicos existentes continuam válidos. Confirma-os:

```bash
crontab -l
systemctl is-active cron
```

Se o cron estiver em falta, instala-o com `bash deploy/install_cron.sh`
(depois de confirmar UTC com `timedatectl`). Não precisas de reinstalar para
esta alteração. Mantém os workflows GitHub de cripto desativados se a VM já
é a responsável pelos scans.

Teste opcional com envio real para os tópicos existentes:

```bash
bash deploy/run.sh crypto config_crypto_4h.yaml
bash deploy/run.sh sweep config_sweep_1h.yaml
```

Não executar ao mesmo tempo que o mesmo scanner no cron. Estes comandos
fazem o universo completo, enviam alertas encontrados e atualizam o estado.

## Validação técnica

```bash
python -m unittest discover -s tests
```

A suíte inclui respostas Bybit simuladas: paginação de instrumentos e velas,
velas fechadas segundo a hora do servidor, limites, falhas e prioridade BTC.
O acesso real à Bybit deve ser validado na VM com `check_bybit.py`.
