"""Testa as guardas do wait_until."""
import unittest, importlib.util
from datetime import datetime, timezone
spec=importlib.util.spec_from_file_location('w','wait_until.py')
w=importlib.util.module_from_spec(spec); spec.loader.exec_module(w)

def at(h,m,s=0): return datetime(2026,9,1,h,m,s,tzinfo=timezone.utc)

class WaitTests(unittest.TestCase):
    def test_espera_quando_arranca_antes(self):
        t,past = w.next_target(at(9,50), 2, 600)
        self.assertFalse(past)
        self.assertEqual((t-at(9,50)).total_seconds()/60, 12.0)

    def test_corre_ja_quando_alvo_passou_dentro_da_tolerancia(self):
        """Atraso tipico do GitHub: nao faz sentido esperar 1h pelo proximo."""
        for minuto in (3, 7, 11):
            t,past = w.next_target(at(10,minuto), 2, 600)
            self.assertTrue(past, f"minuto {minuto}")

    def test_alvo_seguinte_quando_tolerancia_excedida(self):
        t,past = w.next_target(at(10,20), 2, 600)
        self.assertFalse(past)
        self.assertEqual(t.hour, 11)

    def test_espera_longa_deve_ser_recusada_pelo_chamador(self):
        """
        Com atraso grande o alvo salta para a hora seguinte e a espera passa
        de 15 min -- o script corre ja em vez de ficar parado 40 minutos.
        Este e o cenario que tornaria a otimizacao PIOR que nao fazer nada.
        """
        t,_ = w.next_target(at(10,20), 2, 600)
        espera = (t-at(10,20)).total_seconds()
        self.assertGreater(espera, 900)

    def test_minuto_zero_funciona(self):
        t,past = w.next_target(at(9,58), 0, 600)
        self.assertEqual((t.hour, t.minute), (10, 0))

if __name__ == "__main__":
    unittest.main()
