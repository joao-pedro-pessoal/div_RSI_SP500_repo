# Correr os scanners numa VM Google Cloud (gratuito para sempre)

O GitHub Actions descartava cerca de 44% das execucoes agendadas. O cron de
um sistema Linux nao faz isso.

A e2-micro da Google e a unica VM permanentemente gratuita entre os
fornecedores grandes -- as da AWS e Azure expiram ao fim de 12 meses.

---

## Porque GCP e nao AWS Lambda

O Lambda tem limite de **15 minutos** por execucao. Os teus scanners
diarios medidos:

| Scanner | Duracao real |
|---|---|
| Crypto Divergence Daily | 18m38s |
| Sweep Scanner Daily | 14m55s |

O primeiro ja ultrapassa o limite. Seria morto a meio.

---

## 1. Conta

console.cloud.google.com → criar conta.

Cartao obrigatorio para verificacao. **Nao e cobrado** enquanto ficares
dentro dos limites Always Free. Vais receber 300$ de credito de teste; nao
precisas dele e nao ha cobranca automatica quando acabar.

Ao contrario da Oracle, o pais de faturacao pode ser Portugal sem
complicacoes -- e a morada real.

---

## 2. Criar a maquina

Menu → Compute Engine → VM instances → **Create instance**.

| Campo | Valor | Porque |
|---|---|---|
| Region | **us-central1** (Iowa) | a camada gratuita SO existe em us-central1, us-east1 e us-west1 |
| Machine type | **e2-micro** | qualquer outra e paga |
| Boot disk | Ubuntu 22.04 LTS, **30 GB** standard | 30 GB e o maximo gratuito |
| Firewall | deixar tudo desligado | o bot so faz pedidos de saida |

Confirma que aparece a nota de camada gratuita antes de criares. Se a
regiao ou o tipo de maquina estiverem errados, passa a ser cobrado.

**O IP e americano.** Consequencia: a Bybit continua bloqueada. A OKX
funciona -- medimo-lo no diagnostico, o runner do GitHub tambem era
americano e a OKX respondeu 200.

---

## 3. Ligar

Na lista de VMs, clica em **SSH**. Abre um terminal no browser, sem
precisares de gerir chaves. E a forma mais simples.

Se preferires SSH normal, adiciona a tua chave publica em
Compute Engine → Settings → Metadata → SSH keys.

---

## 4. Instalar

    curl -fsSL https://raw.githubusercontent.com/<TU>/<REPO>/main/deploy/setup.sh -o setup.sh
    bash setup.sh https://github.com/<TU>/<REPO>.git

Cria 1 GB de swap, instala python, clona o repositorio, monta o ambiente
virtual e prepara o ficheiro de segredos.

O swap nao e necessario -- medido, o pico de memoria e ~350 MB de 1 GB --
mas custa 1 GB dos 30 de disco e evita surpresas se o universo crescer.

---

## 5. Segredos

    nano ~/scanners/.env

    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=-1003951050134
    TELEGRAM_TOPIC_ID_CRYPTO=778
    TELEGRAM_TOPIC_ID_SWEEP=<o teu topico de varrimentos>

Ctrl+O, Enter, Ctrl+X.

O ficheiro fica com permissoes 600. O token do Telegram da controlo total
do bot a quem o tiver.

---

## 6. Testar antes de automatizar

    cd ~/scanners
    bash deploy/run.sh sweep config_sweep_1h.yaml --dry-run
    cat logs/sweep_config_sweep_1h_*.log

Confirma que a OKX responde (`[provider] 1h: N/100`) e que nao ha erros.

Depois um teste que envia mesmo:

    bash deploy/run.sh sweep config_sweep_1h.yaml

Deves receber o aviso de inicio e o heartbeat no Telegram.

---

## 7. Fuso horario e cron

    sudo timedatectl set-timezone UTC
    bash deploy/install_cron.sh

Os horarios do cron sao em UTC. A VM da Google vem em UTC por omissao, mas
confirma -- se estiver noutro fuso, os scans correm desalinhados do fecho
das velas e os sinais chegam a hora errada.

Verificar:

    crontab -l
    date -u

---

## 8. Desligar os workflows do GitHub

Com a VM a correr, os workflows duplicam tudo. A deduplicacao impede
alertas repetidos, mas confunde os logs.

Actions → cada workflow → menu `...` → **Disable workflow**.

Deixa o "Diagnostico de rede" ativo: e manual e nao custa nada.

Podes reativa-los num clique se a VM tiver problemas.

---

## O que vigiar

**Egress de 1 GB/mes.** E o unico limite que podes ultrapassar sem dar por
isso. O bot envia pouco (pedidos HTTP e mensagens de Telegram), mas
confirma no primeiro mes em Billing → Reports. Descarregar dados NAO conta
-- so o trafego de saida.

**A VM nao adormece.** Ao contrario dos alojamentos gratuitos de web, uma
e2-micro fica ligada. Nao ha reclamacao por inatividade como na Oracle.

**Espaco em disco:** `df -h`. Os logs sao apagados aos 14 dias.

**Se algo parar:**

    systemctl status cron
    grep CRON /var/log/syslog | tail -20
    ls -lt ~/scanners/logs/ | head

**O heartbeat continua a ser o alarme.** Se deixares de receber a mensagem
de conclusao, algo parou -- agora na VM em vez de no GitHub.

---

## Manutencao

O cron faz `git pull` todos os dias as 4h30 UTC. Alteracoes que faças no
GitHub chegam sozinhas a VM. Para forcar:

    cd ~/scanners && git pull
