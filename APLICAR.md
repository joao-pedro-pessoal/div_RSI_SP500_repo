# Execucoes redundantes — substitui sinais_frescos.zip

Inclui tudo o que estava no sinais_frescos (janela 1, alinhamento, envio
agrupado) MAIS a redundancia. Aplica so este.

## O problema medido

O Sweep Scanner 1h ia na execucao #202 quando deviam ser ~360 em duas
semanas. O GitHub descarta cerca de 44% das execucoes agendadas em
periodos de carga -- comportamento documentado, sem garantia nenhuma.

Com alert_age_bars = 1, cada hora saltada perde esse sinal para sempre.

## A solucao

TRES gatilhos por periodo em vez de um:

    56% -> 91% de probabilidade de pelo menos um correr

O --skip-if-recent evita trabalho duplicado: se a primeira tentativa ja
correu com sucesso, as seguintes saem de imediato sem descarregar nada.

Uma execucao FALHADA nao conta como sucesso -- a tentativa seguinte
volta a correr.

## Aplicar

    cd C:\Users\joao2\Downloads\scanner_pronto_para_github\pronto
    tar -xf execucoes_redundantes.zip
    python -m unittest discover -s tests -q
    git add -A
    git commit -m "tres gatilhos por periodo com guarda anti-duplicacao"
    git pull --rebase
    git push

## Se ainda assim falhar

91% ainda deixa ~2 horas por dia sem scan. Se isso incomodar, a unica
solucao real e sair do GitHub Actions:

  Oracle Cloud Always Free — VM ARM gratuita para sempre, cron do sistema
  nao falha. Ressalvas: a capacidade nao e garantida por regiao, os limites
  foram reduzidos em junho de 2026 para 2 nucleos e 12 GB, e instancias
  inativas podem ser reclamadas. Para um script de 3 minutos chega de sobra.

  Raspberry Pi em casa — ~70 euros uma vez, sem mensalidade, e com IP
  europeu a Bybit voltaria a funcionar.
