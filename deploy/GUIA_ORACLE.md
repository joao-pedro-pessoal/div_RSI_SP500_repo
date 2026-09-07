# Correr os scanners numa VM Oracle Cloud (gratuito)

O GitHub Actions descartava cerca de 44% das execucoes agendadas. O cron de
um sistema Linux nao faz isso. Esta e a mudanca.

---

## 1. Criar a conta

cloud.oracle.com → Start for free.

Precisas de telemovel e cartao. **O cartao nao e cobrado** enquanto ficares
dentro dos limites Always Free, mas serve para verificacao.

**A regiao e uma decisao permanente.** Escolhe uma europeia:

| Regiao | Nome |
|---|---|
| Frankfurt | eu-frankfurt-1 |
| Amesterdao | eu-amsterdam-1 |
| Madrid | eu-madrid-1 |

Dois motivos: latencia menor ate Portugal, e o IP deixa de ser americano --
o que significa que a **Bybit voltaria a funcionar**, se um dia quiseres
regressar a ela em vez da OKX.

Nao podes mudar a regiao depois sem criar conta nova.

---

## 2. Criar a maquina

Menu → Compute → Instances → **Create instance**.

| Campo | Valor |
|---|---|
| Image | Ubuntu 22.04 (ou 24.04) |
| Shape | **VM.Standard.A1.Flex** (ARM) |
| OCPUs | 1 |
| Memoria | 6 GB |
| SSH keys | gera e **guarda a chave privada** |

Pede 1 nucleo e nao 4: o script usa uma fracao disso, e pedir menos
aumenta muito a probabilidade de haver capacidade.

**Se der "Out of capacity"** — e comum, nao e erro teu. A capacidade
Always Free nao e garantida por regiao. Tenta:

- outra Availability Domain no mesmo menu
- a forma AMD `VM.Standard.E2.1.Micro` (1 GB RAM). Chega para isto.
- voltar a tentar mais tarde; a capacidade liberta-se

Guarda o **IP publico** que aparece no fim.

---

## 3. Ligar por SSH

No Windows:

    ssh -i C:\caminho\para\chave.key ubuntu@<IP>

Se der erro de permissoes da chave, no PowerShell:

    icacls C:\caminho\para\chave.key /inheritance:r /grant:r "%USERNAME%:R"

---

## 4. Instalar

    curl -fsSL https://raw.githubusercontent.com/<TU>/<REPO>/main/deploy/setup.sh -o setup.sh
    bash setup.sh https://github.com/<TU>/<REPO>.git

Instala python, clona o repositorio, cria o ambiente virtual e prepara o
ficheiro de segredos.

---

## 5. Segredos

    nano ~/scanners/.env

Preenche os quatro valores:

    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=-1003951050134
    TELEGRAM_TOPIC_ID_CRYPTO=778
    TELEGRAM_TOPIC_ID_SWEEP=<o teu topico de varrimentos>

Ctrl+O, Enter, Ctrl+X para gravar.

O ficheiro fica com permissoes 600 -- legivel so por ti. O token do
Telegram da controlo total do bot a quem o tiver.

---

## 6. Testar antes de automatizar

    cd ~/scanners
    bash deploy/run.sh sweep config_sweep_1h.yaml --dry-run
    cat logs/sweep_config_sweep_1h_*.log

O `--dry-run` imprime os alertas em vez de os enviar. Confirma que:

- a OKX responde (`[provider] 1h: N/100`)
- nao ha erros no fim

Depois um teste real, que envia mesmo:

    bash deploy/run.sh sweep config_sweep_1h.yaml

Deves receber o aviso de inicio e o heartbeat no Telegram.

---

## 7. Fuso horario e cron

    sudo timedatectl set-timezone UTC
    bash deploy/install_cron.sh

Os horarios sao em UTC. Se a VM estiver noutro fuso, os scans correm a
hora errada e os alertas ficam desalinhados do fecho das velas.

Verificar:

    crontab -l
    date -u

---

## 8. Desligar os workflows do GitHub

Com a VM a correr, os workflows do GitHub passam a duplicar tudo. A
deduplicacao impede alertas repetidos, mas gasta execucoes e confunde os
logs.

No GitHub, em Actions, para cada workflow: menu `...` → **Disable workflow**.

**Deixa o "Diagnostico de rede" ativo** -- e manual e nao custa nada.

Podes reativa-los se a VM tiver problemas.

---

## Manutencao

**Atualizar o codigo.** O cron faz `git pull` todos os dias as 4h UTC.
Alteracoes que faças no GitHub chegam sozinhas. Para forcar:

    cd ~/scanners && git pull

**Ver o que correu:**

    ls -lt ~/scanners/logs/ | head
    tail -50 ~/scanners/logs/sweep_config_sweep_1h_*.log

Logs com mais de 14 dias sao apagados sozinhos.

**Se a VM parecer parada:**

    systemctl status cron
    grep CRON /var/log/syslog | tail -20

---

## O que vigiar nas primeiras semanas

**Reclamacao por inatividade.** A Oracle pode recuperar instancias Always
Free consideradas inativas. Um scan de hora a hora gera atividade
suficiente, mas vale a pena confirmar que a VM continua de pe ao fim de um
mes.

**Espaco em disco.** `df -h`. Os logs sao limpos aos 14 dias, mas convem
confirmar uma vez.

**O heartbeat continua a ser o teu alarme.** Se deixares de receber a
mensagem de conclusao, algo parou -- agora na VM em vez de no GitHub.
