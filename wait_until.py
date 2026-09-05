#!/usr/bin/env python3
"""
wait_until.py — espera ate ao minuto alvo da hora, com guardas.

PORQUE EXISTE
  Os agendamentos do GitHub Actions nao sao pontuais: um cron para os :05
  pode disparar aos :17. Arrancar mais cedo e esperar reduz esse atraso --
  o job fica na fila mais cedo e, quando arrancar, alinha-se ao minuto certo.

O QUE PODE CORRER MAL SEM GUARDAS
  O gatilho antecipado tambem pode atrasar. Se o job das :50 so arrancar aos
  :10, esperar pelo :02 seguinte seriam 52 minutos parado -- pior do que nao
  fazer nada. Por isso:

    - ja passou o alvo (dentro da tolerancia) -> corre ja
    - a espera excede --max-wait            -> corre ja, com aviso
    - caso contrario                        -> espera, verificando o relogio

  A verificacao periodica existe porque `sleep` nao garante precisao em
  maquinas partilhadas: se o processo for suspenso, o relogio diz a verdade
  e o sleep nao.

  Sai sempre com 0. Este passo nunca deve derrubar o scan.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone


def next_target(now: datetime, minute: int, grace_seconds: int) -> tuple[datetime, bool]:
    """
    Devolve (alvo, ja_passou).

    `ja_passou` fica True quando estamos DEPOIS do alvo desta hora mas ainda
    dentro da tolerancia -- nesse caso corre-se imediatamente em vez de
    esperar uma hora inteira pelo proximo.
    """
    this_hour = now.replace(minute=minute, second=0, microsecond=0)
    if now < this_hour:
        return this_hour, False
    if (now - this_hour).total_seconds() <= grace_seconds:
        return this_hour, True
    return this_hour + timedelta(hours=1), False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minute", type=int, default=2,
                        help="minuto da hora em que o scan deve arrancar (UTC)")
    parser.add_argument("--max-wait", type=int, default=900,
                        help="segundos maximos de espera; acima disto corre ja")
    parser.add_argument("--grace", type=int, default=600,
                        help="segundos apos o alvo em que ainda se considera 'a horas'")
    parser.add_argument("--check-every", type=int, default=60,
                        help="intervalo entre verificacoes do relogio")
    args = parser.parse_args()

    if not 0 <= args.minute <= 59:
        print(f"[wait] minuto invalido ({args.minute}); a correr ja")
        return 0

    now = datetime.now(timezone.utc)
    target, already_past = next_target(now, args.minute, args.grace)
    wait = (target - now).total_seconds()

    print(f"[wait] agora   : {now:%Y-%m-%d %H:%M:%S} UTC")
    print(f"[wait] alvo    : {target:%H:%M:%S} UTC")

    if already_past:
        atraso = (now - target).total_seconds()
        print(f"[wait] o alvo ja passou ha {atraso / 60:.1f} min "
              f"(dentro da tolerancia) -> arranca ja")
        return 0

    if wait > args.max_wait:
        print(f"[wait] espera seria de {wait / 60:.1f} min, acima do limite de "
              f"{args.max_wait / 60:.0f} min -> arranca ja")
        print("[wait] provavel causa: o proprio agendamento atrasou muito")
        return 0

    print(f"[wait] a esperar {wait / 60:.1f} min, a verificar o relogio "
          f"a cada {args.check_every}s")

    while True:
        now = datetime.now(timezone.utc)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            print(f"[wait] {now:%H:%M:%S} UTC -> arranca")
            return 0
        # Nunca dormir para alem do alvo: o relogio manda, nao o sleep.
        chunk = min(args.check_every, remaining)
        print(f"[wait] {now:%H:%M:%S} UTC, faltam {remaining / 60:.1f} min", flush=True)
        time.sleep(chunk)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[wait] interrompido; a arrancar", file=sys.stderr)
        raise SystemExit(0)
