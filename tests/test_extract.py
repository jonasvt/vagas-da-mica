import unittest

from vagas import extract
from vagas.util import parse_date, to_iso


class CompensationTests(unittest.TestCase):
    def comp(self, title, body=""):
        return extract.extract_compensation(title, body)

    def test_range_per_video(self):
        c = self.comp("x", "Budget is around $300 to $700 per video depending on scope.")
        self.assertEqual((c["min"], c["max"], c["currency"], c["unit"]), (300, 700, "$", "video"))

    def test_formato_brasileiro(self):
        c = self.comp("x", "Salário: R$ 4.500,00 a R$ 6.000,00 por mês")
        self.assertEqual((c["currency"], c["min"], c["max"], c["unit"]), ("BRL", 4500, 6000, "month"))
        c = self.comp("x", "Remuneração R$ 45.000 por ano")
        self.assertEqual((c["min"], c["unit"]), (45000, "year"))
        self.assertEqual(self.comp("x", "Pay: $2.5k per month")["min"], 2500)

    def test_dollar_sign_with_usd_suffix(self):
        c = self.comp("x", "Compensation Approximately $180 USD per video.")
        self.assertEqual((c["currency"], c["unit"]), ("USD", "video"))

    def test_hourly(self):
        c = self.comp("Editor $20/hr")
        self.assertEqual((c["min"], c["unit"]), (20, "hour"))

    def test_k_suffix_and_month(self):
        c = self.comp("Retainer", "Pay: $2.5k per month for 12 videos")
        self.assertEqual((c["min"], c["unit"]), (2500, "month"))

    def test_rupees(self):
        c = self.comp("Hiring Video editor for reels (₹250-500/reel)")
        self.assertEqual((c["currency"], c["min"], c["max"], c["unit"]), ("INR", 250, 500, "short"))

    def test_ignores_subscribers_and_revenue(self):
        self.assertIsNone(self.comp("Editor for $10M company", "channel with 677K subs"))

    def test_flat_project(self):
        c = self.comp("[Hiring] Long Form YT Editor ($1100 FLAT)")
        self.assertEqual((c["min"], c["unit"]), (1100, "project"))

    def test_complete_video(self):
        c = self.comp("x", "Budget: $120 USD for the complete video.")
        self.assertEqual((c["min"], c["unit"]), (120, "video"))

    def test_no_currency_is_not_invented(self):
        c = self.comp("Need a reel editor willing to pay 200 per reel")
        self.assertEqual(c["min"], 200)
        self.assertIsNone(c["currency"])

    def test_nothing(self):
        self.assertIsNone(self.comp("Looking for a video editor", "DM me your portfolio"))


class PostKindTests(unittest.TestCase):
    def test_hiring(self):
        for t in ["[HIRING] Video Editor for Comic Book Explainer", "LF Minecraft short form video editor (PAID)",
                  "Video editor required", "Looking for a Video Editor for a New YouTube Channel"]:
            self.assertEqual(extract.classify_post(t, ""), "hiring", t)

    def test_for_hire(self):
        for t in ["[For Hire] Video Editor | Here's some of my work", "I build high-retention Long-Form & Shorts",
                  "🎬 Professional Video Editor | Premiere Pro & After Effects", "I'm looking for a job as a video editor",
                  "How do I find agencies or clients for video editing",
                  "How Much Should I Charge for This Youtube Short Edit"]:
            self.assertEqual(extract.classify_post(t, ""), "for_hire", t)


class RemoteTests(unittest.TestCase):
    def test_restricted(self):
        scope, _ = extract.remote_scope(None, "[HIRING] NEED SHORT FORM VIDEO EDITORS (INDIA ONLY)", "", default_remote=True)
        self.assertEqual(scope, "restricted")
        scope, _ = extract.remote_scope(None, "Editor", "Must be available during EST hours", default_remote=True)
        self.assertEqual(scope, "restricted")

    def test_worldwide(self):
        scope, _ = extract.remote_scope(None, "Editor", "Open to editors worldwide", default_remote=True)
        self.assertEqual(scope, "worldwide")
        scope, _ = extract.remote_scope("Anywhere in the World", "Editor", "hybrid approach to editing", default_remote=True)
        self.assertEqual(scope, "worldwide")

    def test_region_in_title_beats_source_default(self):
        scope, _ = extract.remote_scope("Sem restrição de país (Himalayas)", "Senior Manager, Motion Designer (EU)", "", default_remote=True)
        self.assertEqual(scope, "restricted")
        scope, _ = extract.remote_scope(None, "Video Editor for a Creative Agency (SG-based/Remote)", "", default_remote=True)
        self.assertEqual(scope, "restricted")

    def test_location_field(self):
        scope, _ = extract.remote_scope("United States", "Editor", "", default_remote=True)
        self.assertEqual(scope, "restricted")

    def test_unknown(self):
        scope, _ = extract.remote_scope(None, "Editor", "DM me", default_remote=True)
        self.assertEqual(scope, "unknown")


class LegitTests(unittest.TestCase):
    def test_negation_is_respected(self):
        conf, flags = extract.legitimacy("Editor", "No exclusivity or unpaid availability commitment is required.", None, 0.8)
        self.assertEqual(flags, [])

    def test_flags(self):
        conf, flags = extract.legitimacy("Editor", "Contact me on Telegram. Registration fee of $20 required.", None, 0.8)
        self.assertIn("Contato só por Telegram/WhatsApp", flags)
        self.assertIn("Pede pagamento/taxa do candidato", flags)
        self.assertLess(conf, 0.5)


class DateTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(to_iso("2026-10-27T06:45:12+00:00"), "2026-10-27T06:45:12Z")
        self.assertEqual(to_iso("2026-10-27T03:45:12-03:00"), "2026-10-27T06:45:12Z")
        self.assertEqual(to_iso("Mon, 27 Oct 2026 06:45:12 +0000"), "2026-10-27T06:45:12Z")
        self.assertEqual(to_iso(1793083512), "2026-10-27T06:45:12Z")
        self.assertEqual(to_iso("1793083512000"), "2026-10-27T06:45:12Z")
        self.assertEqual(to_iso("10/27/2026"), "2026-10-27T00:00:00Z")

    def test_invalid_is_none(self):
        self.assertIsNone(parse_date("ontem"))
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date(None))

    def test_discord_snowflake(self):
        from vagas.manual import snowflake_time

        # mensagem do vídeo de referência: EditingHub, 22/10/2026 01:06 (horário de Brasília)
        self.assertEqual(to_iso(snowflake_time("1562678466132971570")), "2026-10-22T04:06:19Z")


if __name__ == "__main__":
    unittest.main()
