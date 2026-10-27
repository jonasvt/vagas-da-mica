import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vagas import busca, db
from vagas.collectors import brasil

REMOTO = {"id": "rem", "name": "Remota", "default_remote": True}
BR = {"id": "br", "name": "BR", "default_remote": False, "pais": "BR"}


def it(title, desc="", loc=None, i=None, **kw):
    d = {"source_item_id": i or title, "title": title, "description": desc, "location_text": loc, "tags": [],
         "source_url": f"https://x.test/{abs(hash(title))}", "published_at": "2099-01-01T00:00:00Z", "raw": {}}
    d.update(kw)
    return d


class RemotoTests(unittest.TestCase):
    def test_hibrido_e_presencial(self):
        self.assertEqual(busca.modo_trabalho(it("[Híbrido] Desenvolvedor Java"), BR), "hybrid")
        self.assertEqual(busca.modo_trabalho(it("Backend Engineer", "This is a hybrid role, 3 days in office."), REMOTO), "hybrid")
        self.assertEqual(busca.modo_trabalho(it("Analista de Dados - Presencial"), BR), "onsite")
        self.assertEqual(busca.modo_trabalho(it("Dev", "Trabalho em modelo híbrido em SP"), BR), "hybrid")

    def test_remoto(self):
        self.assertEqual(busca.modo_trabalho(it("[Remoto] Back-end Developer JR"), BR), "remote")
        self.assertEqual(busca.modo_trabalho(it("Hybrid Cloud Engineer", "We run a hybrid cloud architecture."), REMOTO), "remote")
        self.assertEqual(busca.modo_trabalho(it("Data Engineer", "Fully remote. No on-site work required."), REMOTO), "remote")
        self.assertEqual(busca.modo_trabalho(it("Dev", raw={"workplace_type": "remote"}), BR), "remote")
        # falsos positivos encontrados no banco real
        self.assertEqual(busca.modo_trabalho(it("Motion Designer", "Videos that help users beyond what in-person visits can achieve."), REMOTO), "remote")
        self.assertEqual(busca.modo_trabalho(it("Cloud Engineer", "- Hybrid cloud experience\n- AWS"), REMOTO), "remote")
        self.assertEqual(busca.modo_trabalho(it("Content Creator", "Full Time - On site opportunity | Nasr City"), REMOTO), "onsite")

    def test_fonte_br_sem_indicacao_nao_entra(self):
        self.assertEqual(busca.modo_trabalho(it("[Joinville/SC] Back-end Developer"), BR), "unknown")


class PaisTests(unittest.TestCase):
    def ok(self, **kw):
        return busca.aceita_brasil(it(kw.pop("title", "Python Developer"), **kw), REMOTO)[0]

    def test_aceita(self):
        for loc in (None, "Worldwide", "Anywhere in the World", "Americas", "LATAM", "Brazil, Mexico", "Remote",
                    "Sem restrição de país (Himalayas)", "United States, Brazil"):
            self.assertTrue(self.ok(loc=loc), loc)
        self.assertTrue(self.ok(title="Data Engineer (LATAM)"))
        self.assertTrue(self.ok(desc="Contact us based on your availability. Must work in the same time zone as the team."))
        self.assertTrue(self.ok(desc="We hire engineers across Latin America-based teams."))
        self.assertTrue(self.ok(desc="We are a New Zealand-based brand making cinematic trailers."))
        self.assertTrue(self.ok(desc="Join a US-based startup. Send your portfolio."))

    def test_recusa_so_quando_explicito(self):
        for loc in ("USA Only", "Remote - US", "Europe", "United Kingdom", "London Area", "USA, Canada"):
            self.assertFalse(self.ok(loc=loc), loc)
        self.assertFalse(self.ok(title="Senior Manager, Motion Designer (EU)"))
        self.assertFalse(self.ok(title="[HIRING] Web Developers (UI UX design Frontend related) - US Remote"))
        self.assertFalse(self.ok(title="[Hiring] Remote Customer Support Representative – US/Canada – $25–35/hr"))
        self.assertTrue(self.ok(title="Video editor for UK-based YouTuber"))
        self.assertTrue(self.ok(title="Social Media Manager | Remote | LATAM Only"))
        self.assertFalse(self.ok(desc="Candidates must be based in the United States."))
        self.assertFalse(self.ok(desc="This role is US-based only."))
        self.assertFalse(self.ok(desc="You must be eligible to work in the UK."))

    def test_fonte_brasileira_sempre_aceita(self):
        self.assertTrue(busca.aceita_brasil(it("Dev", loc="São Paulo"), BR)[0])


class PalavrasTests(unittest.TestCase):
    def test_acentos_hifen_e_virgula(self):
        j = {"title": "Cientista de Dados Pleno", "description": ""}
        self.assertEqual(busca.casa(j, busca.separar("cientista de dados"), []), "titulo")
        j = {"title": "Desenvolvedor Back-end Python", "description": ""}
        self.assertEqual(busca.casa(j, busca.separar("java, backend"), []), "titulo")
        j = {"title": "Engenheiro de Software", "description": "Stack: Python, FastAPI e PostgreSQL"}
        self.assertEqual(busca.casa(j, ["fastapi"], []), "texto")

    def test_frase_exata(self):
        def c(titulo, termo, desc=""):
            return busca.casa({"title": titulo, "description": desc}, [termo], [])
        self.assertIsNone(c("Community Engagement Manager, Data Centers", "community manager"))
        self.assertIsNone(c("Category Manager - Sneakers", "community manager", "Join our community of sneaker fans."))
        self.assertEqual(c("Social Media & Community Manager", "community manager"), "titulo")
        self.assertEqual(c("Social-Media Lead", "social media"), "titulo")
        self.assertEqual(c("Engenheiro(a) de Dados Pleno", "engenheiro de dados"), "titulo")
        self.assertEqual(c("Back end Developer", "backend"), None)  # "back end" separado não vira "backend"
        self.assertEqual(c("Back-end Developer", "back end"), "titulo")
        # ponto que o teclado do celular coloca sozinho e plural
        self.assertEqual(c("Community Manager", "Community Manager."), "titulo")
        self.assertEqual(c("Community Manager", "Community Managers"), "titulo")
        self.assertEqual(c("Video Editors Wanted", "video editor"), "titulo")
        self.assertEqual(c("Cientista de Dados", "cientistas de dados"), "titulo")
        self.assertEqual(c("Senior .NET Developer", ".net"), "titulo")
        self.assertIsNone(c("Business Analyst", "busines"))

    def test_excluir_e_palavra_inteira(self):
        j = {"title": "Senior Python Developer", "description": ""}
        self.assertIsNone(busca.casa(j, ["python"], ["senior"]))
        j = {"title": "Database Administrator", "description": ""}
        self.assertIsNone(busca.casa(j, ["data"], []))


class MotorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(self.tmp / "t.db")
        db.sync_sources(self.conn, [{"id": "rem", "name": "Remota", "method": "x"}])

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingerir(self, items, src=REMOTO):
        return busca.ingerir(self.conn, {**src, "modo": "feed"}, items, {})

    def test_so_remota_e_aberta_ao_brasil_entra(self):
        s = self.ingerir([it("Python Developer", loc="Worldwide"), it("Python Dev (hybrid)"),
                          it("Python Engineer", loc="USA Only"), it("Data Analyst", "Office-based in NYC")])
        self.assertEqual((s["new"], s["skipped"]), (1, 3))

    def test_primeira_carga_nao_avisa_depois_avisa(self):
        aid = busca.criar_alerta(self.conn, 111, "python", canal_id=900)
        self.ingerir([it("Python Developer", i="a")])
        self.assertEqual(busca.preparar_envios(self.conn), {})  # primeira sincronização = carga inicial
        self.ingerir([it("Senior Python Engineer", i="b")])
        env = busca.preparar_envios(self.conn)
        self.assertEqual([j["title"] for j in env["900"]], ["Senior Python Engineer"])
        busca.marcar_enviadas(self.conn, "900", [j["id"] for j in env["900"]])
        self.assertEqual(busca.preparar_envios(self.conn), {})  # não repete
        self.assertTrue(busca.apagar_alerta(self.conn, 111, aid))

    def test_cada_canal_com_seus_alertas(self):
        busca.criar_alerta(self.conn, 1, "python, cientista de dados", "senior", canal_id=901)
        busca.criar_alerta(self.conn, 2, "video editor", canal_id=902)
        self.ingerir([])  # marca a fonte como já sincronizada
        self.ingerir([it("Cientista de Dados Jr", i="c"), it("Senior Python Dev", i="d"), it("YouTube Video Editor", i="e")])
        env = busca.preparar_envios(self.conn)
        self.assertEqual([j["title"] for j in env["901"]], ["Cientista de Dados Jr"])
        self.assertEqual([j["title"] for j in env["902"]], ["YouTube Video Editor"])

    def test_dois_alertas_no_mesmo_canal_nao_repetem_vaga(self):
        busca.criar_alerta(self.conn, 1, "video editor", canal_id=903)
        busca.criar_alerta(self.conn, 1, "youtube", canal_id=903)
        self.ingerir([])
        self.ingerir([it("YouTube Video Editor", i="f")])
        self.assertEqual(len(busca.preparar_envios(self.conn)["903"]), 1)
        self.assertEqual(busca.desativar_canal(self.conn, 903), 2)  # canal apagado
        self.assertEqual(busca.preparar_envios(self.conn), {})

    def test_classificacao_reddit(self):
        src = {**REMOTO, "classificar_posts": True}
        s = self.ingerir([it("[For Hire] Video editor, check my portfolio"), it("[HIRING] YouTube editor")], src)
        self.assertEqual((s["new"], s["skipped"]), (1, 1))

    def test_sub_geral_so_aceita_contratacao(self):
        src = {**REMOTO, "id": "geral", "classificar_posts": True, "so_contratacao": True}
        s = self.ingerir([it("Do hashtags still contribute to reach on FB?"), it("[Hiring] Social Media Manager, remote worldwide")], src)
        self.assertEqual((s["new"], s["skipped"]), (1, 1))

    def test_busca_titulo_antes_do_texto(self):
        self.ingerir([it("Backend Engineer", "We use Python daily", i="f"), it("Python Developer", i="g")])
        jobs, total = busca.buscar(self.conn, "python")
        self.assertEqual(total, 2)
        self.assertEqual(jobs[0]["title"], "Python Developer")
        self.assertEqual(jobs[1]["_onde"], "texto")

    def test_vaga_encaminhada(self):
        link = "https://discord.com/channels/1078753591264809070/1562678466132971570/1562678466132971570"
        texto = "Long Form Gaming YouTube Editor for 677K Channel\nRemote, worldwide. Budget $300-$700 per video."
        r = busca.importar(self.conn, texto, link, "2026-10-22T04:06:19Z")
        self.assertEqual(r["status"], "salva")
        self.assertEqual(r["job"]["published_at"], "2026-10-22T04:06:19Z")
        self.assertEqual(r["job"]["comp_max"], 700)
        self.assertEqual(busca.importar(self.conn, texto, link)["status"], "repetida")
        self.assertEqual(busca.importar(self.conn, "Editor de vídeo presencial em Curitiba, salário a combinar", None)["status"], "nao_remota")
        self.assertEqual(busca.importar(self.conn, "Remote video editor needed. US only applicants please.", None)["status"], "outro_pais")

    def test_limpeza_do_banco(self):
        db.sync_sources(self.conn, [{"id": "velha", "name": "velha", "method": "x"}])
        rows = [("1", "Python Developer", "Worldwide", None), ("2", "Video Editor", "London Area", None),
                ("3", "Editor (Hybrid)", None, None)]
        for sid, title, loc, dup in rows:
            self.conn.execute("INSERT INTO jobs(source_id, source_item_id, title, location_text, discovered_at) VALUES ('velha',?,?,?,'2026-10-27T00:00:00Z')",
                              (sid, title, loc))
        self.conn.execute("INSERT INTO jobs(source_id, source_item_id, title, discovered_at, duplicate_of) VALUES ('velha','4','Editor (Hybrid) repost','2026-10-27T00:00:00Z',3)")
        # linha do painel antigo: texto explicativo no campo de local, local real (vazio) no raw
        self.conn.execute("""INSERT INTO jobs(source_id, source_item_id, title, location_text, raw, discovered_at)
                             VALUES ('velha','5','[HIRING] YouTube editor','local não informado (remoto implícito)',
                                     '{"location_text": null, "tags": []}','2026-10-27T00:00:00Z')""")
        self.conn.commit()
        res = busca.remover_nao_aceitas(self.conn, {"fontes": []})
        restantes = sorted(r[0] for r in self.conn.execute("SELECT title FROM jobs"))
        self.assertEqual(restantes, ["Python Developer", "[HIRING] YouTube editor"])
        self.assertEqual(res["apagadas"], 3)


class LinkedInTests(unittest.TestCase):
    POST = "https://www.linkedin.com/posts/fulana_vagas-python-activity-7390000000000000000-abcd"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(self.tmp / "t.db")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def imp(self, texto, link=None):
        return busca.importar(self.conn, texto, link or self.POST)

    def test_post_de_vaga_completo_entra(self):
        r = self.imp("Estamos contratando! Vaga para Desenvolvedora Python Pleno, 100% remoto.\n"
                     "Envie seu currículo para vagas@empresa.com.br #vagas")
        self.assertEqual(r["status"], "salva")
        self.assertEqual(r["job"]["community"], "LinkedIn (post)")

    def test_post_sem_contato_nao_entra(self):
        r = self.imp("Estamos contratando! Vaga para Desenvolvedora Python Pleno, 100% remoto. #vagas")
        self.assertEqual(r["status"], "sem_certeza")

    def test_pessoa_procurando_emprego_nao_entra(self):
        r = self.imp("Estou em busca de uma nova oportunidade como dev Python, remoto. Me chame no inbox! #opentowork")
        self.assertEqual(r["status"], "sem_certeza")

    def test_post_sem_remoto_escrito_nao_entra(self):
        r = self.imp("We're hiring a Python Developer in our team. Send your CV to jobs@acme.com")
        self.assertEqual((r["status"], r["modo"]), ("nao_remota", "unknown"))

    def test_post_hibrido_nao_entra(self):
        r = self.imp("Estamos contratando dev Python, modelo híbrido em SP (remoto às sextas). Candidate-se: https://acme.gupy.io/job/1")
        self.assertEqual(r["status"], "nao_remota")

    def test_link_preferido_e_o_do_post(self):
        txt = f"Vaga remota! Candidate-se em https://forms.gle/abc\n{self.POST}"
        self.assertEqual(busca.link_da_vaga(txt), self.POST)


class EmailAlertaTests(unittest.TestCase):
    HTML = """<html><body><table>
      <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=x">Desenvolvedora Python Pleno</a></td></tr>
      <tr><td>ACME Tecnologia · Brasil (Remoto)</td></tr>
      <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trk=y">Ver vaga</a></td></tr>
      <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4098765432/">Data Engineer</a></td></tr>
      <tr><td>Beta Corp · São Paulo (Híbrido)</td></tr>
      <tr><td><a href="https://www.upwork.com/jobs/~0123456789abcdef01?source=rss">Python script for data cleaning</a></td></tr>
      <tr><td><a href="https://www.linkedin.com/comm/psettings/email-unsubscribe">Unsubscribe</a></td></tr>
    </table></body></html>"""

    def test_parse(self):
        from vagas.collectors import email_alertas

        items = email_alertas.parse_alerta(self.HTML, None)
        por_id = {i["source_item_id"]: i for i in items}
        self.assertEqual(set(por_id), {"linkedin:4012345678", "linkedin:4098765432", "upwork:0123456789abcdef01"})
        self.assertEqual(por_id["linkedin:4012345678"]["title"], "Desenvolvedora Python Pleno")
        self.assertEqual(por_id["linkedin:4012345678"]["source_url"], "https://www.linkedin.com/jobs/view/4012345678/")
        modos = {k: busca.modo_trabalho(v, {"default_remote": False}) for k, v in por_id.items()}
        self.assertEqual(modos, {"linkedin:4012345678": "remote", "linkedin:4098765432": "hybrid", "upwork:0123456789abcdef01": "remote"})

    def test_sem_configuracao_fica_parada(self):
        from vagas.collectors import email_alertas
        from vagas.http import CollectError

        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(CollectError):
            email_alertas.email_alertas({"params": {}})
        cfg = {"fontes": [{"id": "email-alertas", "name": "e", "method": "email_alertas", "requer_env": ["IMAP_USER"]}]}
        tmp = Path(tempfile.mkdtemp())
        conn = db.connect(tmp / "t.db")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(busca.coletar(conn, cfg, force=True), [])
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


class ColetoresBrTests(unittest.TestCase):
    def test_github_issues(self):
        fake = [{"number": 7, "title": "[Remoto] Back-end Python Jr", "body": "Vaga", "html_url": "https://github.com/backend-br/vagas/issues/7",
                 "created_at": "2026-10-26T14:47:20Z", "user": {"login": "rh"}, "labels": [{"name": "Remoto"}, {"name": "Júnior"}]},
                {"number": 8, "title": "PR", "pull_request": {}}]
        with mock.patch("vagas.http.get_json", return_value=fake):
            items = brasil.github_issues({"params": {"repos": ["backend-br/vagas"]}})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_item_id"], "backend-br/vagas#7")
        self.assertEqual(items[0]["tags"], ["Remoto", "Júnior"])

    def test_gupy_modelo_de_trabalho(self):
        fake = {"data": [{"id": 1, "name": "Dev Python", "workplaceType": "remote", "jobUrl": "https://a.gupy.io/job/1",
                          "careerPageName": "Empresa", "publishedDate": "2026-10-26T14:16:19.305Z", "city": "", "state": ""},
                         {"id": 2, "name": "Dev Java", "workplaceType": "hybrid", "jobUrl": "https://a.gupy.io/job/2"}]}
        with mock.patch("vagas.http.get_json", return_value=fake):
            items = brasil.gupy({"params": {"queries": ["python"]}})
        self.assertEqual([busca.modo_trabalho(i, BR) for i in items], ["remote", "hybrid"])


class BotTests(unittest.TestCase):
    def test_comandos_e_cartao(self):
        from vagas import bot

        b = bot.BotVagas(conn=None, cfg={})
        self.assertEqual(sorted(c.name for c in b.tree.get_commands()), ["ajuda", "alerta", "alertas", "apagar", "vagas"])
        e = bot.job_embed({"title": "Python Dev", "source_url": "https://x.test/1", "company": "ACME", "comp_min": 3000,
                           "comp_max": 4000, "comp_currency": "USD", "comp_unit": "month", "location_text": "Worldwide",
                           "published_at": None, "discovered_at": None, "community": "Himalayas"})
        self.assertIn("USD 3.000–4.000/mês", e.description)

    def test_posta_no_canal_sem_marcar_ninguem(self):
        import asyncio

        from vagas import bot

        tmp = Path(tempfile.mkdtemp())
        conn = db.connect(tmp / "t.db")
        busca.criar_alerta(conn, 1, "python", canal_id=555)
        busca.ingerir(conn, {**REMOTO, "modo": "feed"}, [], {})
        busca.ingerir(conn, {**REMOTO, "modo": "feed"}, [it("Python Developer", i="p")], {})
        b = bot.BotVagas(conn=conn, cfg={})
        canal = mock.Mock()
        canal.send = mock.AsyncMock()
        with mock.patch.object(b, "get_channel", return_value=canal):
            asyncio.run(b.enviar_alertas())
            asyncio.run(b.enviar_alertas())  # segunda vez: nada novo
        self.assertEqual(canal.send.await_count, 1)
        kw = canal.send.await_args.kwargs
        self.assertEqual(kw["embeds"][0].title, "Python Developer")
        am = kw["allowed_mentions"]
        self.assertFalse(am.everyone or am.users or am.roles)
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)

    def test_texto_do_encaminhamento(self):
        from vagas import bot

        snap = mock.Mock(content="Vaga de editor", embeds=[mock.Mock(title="Título", description="Remoto")])
        self.assertEqual(bot._texto_do_snapshot(snap), "Vaga de editor\nTítulo\nRemoto")


if __name__ == "__main__":
    unittest.main()
