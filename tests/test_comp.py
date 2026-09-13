"""
Testes do COMP. Sem rede: as series sao construidas a mao.

O que estes testes protegem, por ordem de importancia:
  1. Uma cunha com fecho fora produz sinal.
  2. A MESMA cunha sem esse fecho nao produz nada. E o teste que apanha o
     erro mais provavel: se a busca de estrutura passar a incluir a barra do
     rompimento, o primeiro caso deixa de dar sinal (a linha morre) -- e se
     o buffer for ignorado, o segundo passa a dar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_scanner.comp import detect, wilder_atr
from crypto_scanner.config import CompConfig


def cunha(n: int = 200, breakout: bool = True) -> pd.DataFrame:
    """Topos a descer e fundos a subir: compressao com apex proximo."""
    idx = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    base, rows = 100.0, []
    for i in range(n):
        teto = base + 8.0 - i * 0.035
        piso = base - 8.0 + i * 0.030
        meio, amp = (teto + piso) / 2, (teto - piso) / 2
        c = meio + amp * np.cos(i * np.pi / 6) * 0.92
        o = meio + amp * np.cos((i - 1) * np.pi / 6) * 0.92
        rows.append((o, max(o, c) + amp * 0.06, min(o, c) - amp * 0.06, c))
    if breakout:
        o, _, l, _ = rows[-1]
        teto = base + 8.0 - (n - 1) * 0.035
        rows[-1] = (o, teto + 2.0, l, teto + 1.5)
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


def test_atr_tem_semente_de_wilder():
    atr = wilder_atr(cunha(), 14)
    assert np.isfinite(atr.iloc[-1]) and atr.iloc[-1] > 0
    assert atr.iloc[:13].isna().all()          # sem valor antes da semente


def test_rompimento_de_cunha_produz_sinal():
    sinais = detect("TEST-USDT-SWAP", "4h", cunha(), CompConfig())
    assert len(sinais) == 1
    s = sinais[0]
    assert s.kind == "breakout_up"
    assert s.source == "trendline"
    assert s.close > s.level
    assert s.touches >= 3


def test_sem_fecho_fora_nao_ha_sinal():
    assert detect("TEST-USDT-SWAP", "4h", cunha(breakout=False), CompConfig()) == []


def test_compressao_reconhecida():
    s = detect("TEST-USDT-SWAP", "4h", cunha(), CompConfig())[0]
    assert s.in_compression
    assert s.compression_type == "TL x TL"


def test_signal_id_estavel_e_unico_por_vela():
    a = detect("TEST-USDT-SWAP", "4h", cunha(), CompConfig())[0]
    b = detect("TEST-USDT-SWAP", "4h", cunha(), CompConfig())[0]
    assert a.signal_id == b.signal_id           # deduplicacao entre execucoes
    assert a.bar_time.isoformat() in a.signal_id


def test_historico_curto_nao_rebenta():
    assert detect("X", "4h", cunha(n=40), CompConfig()) == []
