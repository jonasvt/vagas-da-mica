import unittest
from unittest import mock

from vagas import site
from vagas.http import CollectError


def item(i, title, loc=None, desc="Remote role.", company="ACME", published="2099-01-01T00:00:00Z", url=None):
    return {"source_item_id": i, "title": title, "description": desc, "location_text": loc, "company": company,
            "author": None, "source_url": url or f"https://x.test/{i}", "published_at": published, "tags": [], "raw": {},
            "comp": None}


CFG = {"dias_guardar": 30, "fontes": [
    {"id": "a", "name": "Fonte A", "method": "fa", "modo": "feed", "interval_min": 60, "default_remote": True},
    {"id": "b", "name": "Fonte B", "method": "fb", "modo": "termos", "interval_min": 360, "default_remote": True},
]}


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.feeds = {"fa": [], "fb": []}
        self.chamadas = {"fa": 0, "fb": 0}

        def coletor(nome):
            def f(src):
                self.chamadas[nome] += 1
                if isinstance(self.feeds[nome], Exception):
                    raise self.feeds[nome]
                return self.feeds[nome]
            return f

        p1 = mock.patch.object(site.config, "load_json", return_value=CFG)
        p2 = mock.patch.object(site, "get_collector", side_effect=lambda m: coletor(m))
        p3 = mock.patch.object(site.time, "sleep")
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)

    def test_so_remota_e_aberta_ao_brasil(self):
        self.feeds["fa"] = [item("1", "Python Developer", "Worldwide"), item("2", "Data Engineer", "USA Only"),
                            item("3", "Backend Dev (Hybrid)")]
        out = site.gerar({})
        self.assertEqual([v["titulo"] for v in out["vagas"]], ["Python Developer"])
        self.assertEqual(out["vagas"][0]["local"], "Qualquer país")

    def test_mantem_descoberta_e_nao_duplica(self):
        self.feeds["fa"] = [item("1", "Python Developer")]
        primeira = site.gerar({})
        descoberta = primeira["vagas"][0]["descoberta"]
        self.feeds["fb"] = [item("9", "Python Developer", url="https://x.test/1")]  # mesma vaga em outra fonte
        segunda = site.gerar(primeira, force=True)
        self.assertEqual(len(segunda["vagas"]), 1)
        self.assertEqual(segunda["vagas"][0]["descoberta"], descoberta)

    def test_respeita_intervalo_da_fonte(self):
        out = site.gerar({})
        site.gerar(out)  # logo em seguida: nenhuma fonte está no horário
        self.assertEqual(self.chamadas, {"fa": 1, "fb": 1})

    def test_fonte_quebrada_nao_para_as_outras(self):
        self.feeds["fa"] = CollectError("fora do ar")
        self.feeds["fb"] = [item("5", "Data Analyst")]
        out = site.gerar({})
        self.assertFalse(out["fontes"]["a"]["ok"])
        self.assertEqual([v["titulo"] for v in out["vagas"]], ["Data Analyst"])

    def test_descarta_antigas(self):
        self.feeds["fa"] = [item("1", "Old job", published="2020-01-01T00:00:00Z"), item("2", "New job")]
        self.assertEqual([v["titulo"] for v in site.gerar({})["vagas"]], ["New job"])

    def test_local_amigavel(self):
        self.assertEqual(site.local_amigavel("Brazil", {}), "Brasil")
        self.assertEqual(site.local_amigavel("Albania, Andorra, Angola, Argentina, Brazil, Chile", {}), "Vários países (inclui o Brasil)")
        self.assertEqual(site.local_amigavel(None, {"pais": "BR"}), "Brasil")
        self.assertEqual(site.local_amigavel("Americas", {}), "Americas")

    def test_valor_sem_unidade_e_baixo_nao_aparece(self):
        self.assertIsNone(site.formatar_valor({"min": 45, "max": 45, "currency": "BRL", "unit": "unknown"}))
        self.assertEqual(site.formatar_valor({"min": 4500, "max": 6000, "currency": "BRL", "unit": "month"}), "BRL 4.500–6.000/mês")


if __name__ == "__main__":
    unittest.main()
