# Sinais frescos — substitui envio_agrupado.zip e alinhamento_horario.zip

## O problema

Sinais de 5 horas atras a chegar como se fossem novos.

Tres causas, todas corrigidas aqui:

1. JANELA DE 3 BARRAS. Existia para tolerar uma execucao falhada, mas era
   ela que deixava passar sinais velhos. Agora e 1: so a vela que acabou
   de fechar.

2. SEM ALINHAMENTO. Uma execucao as :50 reportava a vela que fechou ha 50
   minutos. Agora arranca as :50 e espera pelo :02.

3. 73 MENSAGENS INDIVIDUAIS. Medido: 41 segundos por mensagem (contra 3.5s
   de throttle) por causa dos 429 do Telegram, e o job era morto pelo
   timeout de 25 min antes de acabar. Agora sao 10 sinais por mensagem.

## Aplicar

    cd C:\Users\joao2\Downloads\scanner_pronto_para_github\pronto
    tar -xf sinais_frescos.zip
    python -m unittest discover -s tests -q
    git add -A
    git commit -m "sinais frescos: janela 1, alinhamento e envio agrupado"
    git pull --rebase
    git push

## Custo assumido

Com janela 1, uma execucao que nao corra perde o sinal dessa vela PARA
SEMPRE. Nao ha recuperacao. Foi decisao explicita: um sinal velho nao vale
nada, e um sinal perdido custa menos que um enganador.

Isto torna o agendamento critico. Se os workflows continuarem a nao correr
sozinhos, vais receber menos sinais do que antes -- mas os que receberes
sao todos frescos.

## Como verificar

Cada mensagem passa a dizer a idade da vela:

    🌊 3 varrimentos — 1h · há 4 min

Se vires "há 3.0h" numa vela de 1h, alguma execucao falhou.
