import json
import shutil
import tempfile
import unittest
from pathlib import Path

from vagas import collectors, db, dedupe, notify, pipeline, queries
from vagas.collectors import reddit_rss
from vagas.config import load_profile
from vagas.http import CollectError

FIXTURES = Path(__file__).parent / "fixtures"


def item(i, title, body, author="u/alice", sub="r/HireAnEditor", published="2099-01-01T00:00:00Z"):
    return {
        "source_item_id": f"t3_{i}", "title": title, "description": body,
        "source_url": f"https://www.reddit.com/r/x/comments/{i}/slug/", "apply_url": None,
        "published_at": published, "author": author, "company": None, "community": sub,
        "location_text": None, "tags": [], "comp": None, "employment_type": None, "post_kind_hint": None, "raw": {},
    }


BODY = ("I run a 300K gaming channel and need a long-form YouTube editor for 2 videos per week. "
        "Budget is $150 per video, Premiere Pro preferred. Send your portfolio and rates. Long term work for the right person.")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "t.db"
        self.conn = db.connect(self.db_path)
        self.profile = load_profile()
        self.feed = {"a": [], "b": []}
        collectors.COLLECTORS["fake_a"] = lambda src: self.feed["a"]
        collectors.COLLECTORS["fake_b"] = lambda src: self.feed["b"]

        def boom(src):
            raise CollectError("fonte fora do ar", transient=False, http_status=503)

        collectors.COLLECTORS["fake_boom"] = boom
        self.sources = [
            {"id": "a", "name": "Fonte A", "method": "fake_a", "interval_min": 10, "relevance_gate": "editor_community"},
            {"id": "boom", "name": "Quebrada", "method": "fake_boom", "interval_min": 10},
            {"id": "b", "name": "Fonte B", "method": "fake_b", "interval_min": 10, "relevance_gate": "editor_community"},
        ]

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def collect(self, **kw):
        return pipeline.collect(self.conn, self.sources, self.profile, force=True, notify_enabled=False, **kw)

    def test_failing_source_does_not_stop_others(self):
        self.feed["a"] = [item(1, "[HIRING] YouTube editor", BODY)]
        self.feed["b"] = [item(2, "[HIRING] Shorts editor", BODY.replace("long-form", "shorts"), author="u/bob")]
        res = {r["source"]: r for r in self.collect()}
        self.assertEqual(res["boom"]["status"], "error")
        self.assertEqual(res["a"]["new"], 1)
        self.assertEqual(res["b"]["new"], 1)
        row = self.conn.execute("SELECT consecutive_failures, last_error, next_check_at FROM sources WHERE id='boom'").fetchone()
        self.assertEqual(row["consecutive_failures"], 1)
        self.assertIn("fora do ar", row["last_error"])
        self.assertIsNotNone(row["next_check_at"])
        errs = queries.runs(self.conn, errors_only=True)
        self.assertEqual(len(errs), 1)

    def test_backfill_then_live(self):
        self.feed["a"] = [item(1, "[HIRING] YouTube editor", BODY)]
        self.collect(only=["a"])
        self.feed["a"].append(item(3, "[HIRING] Podcast editor", BODY.replace("gaming", "podcast"), author="u/carol"))
        self.collect(only=["a"])
        kinds = dict(self.conn.execute("SELECT source_item_id, discovery_kind FROM jobs").fetchall())
        self.assertEqual(kinds, {"t3_1": "backfill", "t3_3": "live"})

    def test_old_post_seen_late(self):
        self.collect(only=["a"])  # primeira sincronização vazia
        self.feed["a"] = [item(4, "[HIRING] editor", BODY, published="2020-01-01T00:00:00Z")]
        self.collect(only=["a"])
        self.assertEqual(self.conn.execute("SELECT discovery_kind FROM jobs").fetchone()[0], "late")

    def test_repeat_is_not_reinserted_and_update_detected(self):
        self.feed["a"] = [item(1, "[HIRING] YouTube editor", BODY)]
        self.collect(only=["a"])
        r = self.collect(only=["a"])[0]
        self.assertEqual((r["new"], r["updated"]), (0, 0))
        self.feed["a"] = [item(1, "[HIRING] YouTube editor", BODY.replace("$150", "$250"))]
        r = self.collect(only=["a"])[0]
        self.assertEqual(r["updated"], 1)
        self.assertEqual(self.conn.execute("SELECT comp_min FROM jobs").fetchone()[0], 250)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    def test_crosspost_marked_as_duplicate_not_deleted(self):
        self.feed["a"] = [item(1, "[HIRING] Video Editor / Partner for History Channel (Shorts)", BODY, sub="r/HireAnEditor")]
        self.feed["b"] = [item(2, "[Hiring] Video Editor / Partner for History Channel (Shorts)", BODY, sub="r/VideoEditor_forhire")]
        self.collect()
        rows = self.conn.execute("SELECT id, duplicate_of, dup_reason FROM jobs ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["duplicate_of"])
        self.assertEqual(rows[1]["duplicate_of"], rows[0]["id"])
        detail = queries.job_detail(self.conn, rows[0]["id"])
        self.assertEqual(len(detail["related"]), 1)
        listed = queries.list_jobs(self.conn, {"view": "all"})
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["dup_count"], 1)

    def test_similar_text_different_title_is_duplicate(self):
        self.feed["a"] = [item(1, "Need a gaming editor", BODY, author="u/dave")]
        self.feed["b"] = [item(2, "Gaming YouTube editor wanted - long term", BODY + " Thanks!", author="u/dave")]
        self.collect()
        dup = self.conn.execute("SELECT duplicate_of FROM jobs WHERE source_item_id='t3_2'").fetchone()[0]
        self.assertIsNotNone(dup)

    def test_different_jobs_not_merged(self):
        self.feed["a"] = [item(1, "[HIRING] YouTube editor", BODY, author="u/a1")]
        self.feed["b"] = [item(2, "[HIRING] Podcast clipper for TikTok", "We need clips from our weekly podcast, $20 per short, CapCut ok, beginners welcome, ongoing work with our team every week.", author="u/b2")]
        self.collect()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM jobs WHERE duplicate_of IS NULL").fetchone()[0], 2)

    def test_for_hire_posts_are_skipped(self):
        self.feed["a"] = [item(1, "[For Hire] Video editor, check my portfolio", "I'm a video editor with 5 years experience")]
        r = self.collect(only=["a"])[0]
        self.assertEqual((r["new"], r["skipped"]), (0, 1))

    def test_persistence_across_restart(self):
        self.feed["a"] = [item(1, "[HIRING] YouTube editor", BODY)]
        self.collect(only=["a"])
        jid = self.conn.execute("SELECT id FROM jobs").fetchone()[0]
        queries.update_job(self.conn, jid, {"user_status": "applied", "notes": "mandei portfólio", "saved": 1})
        self.conn.close()
        self.conn = db.connect(self.db_path)
        j = queries.job_detail(self.conn, jid)
        self.assertEqual((j["user_status"], j["notes"], j["saved"]), ("applied", "mandei portfólio", 1))
        self.assertIsNotNone(j["applied_at"])

    def test_notify_once(self):
        self.collect(only=["a"])  # primeira sync vazia => próximas são "live"
        self.feed["a"] = [item(1, "[HIRING] Long-form YouTube gaming editor", BODY + " Worldwide applicants welcome.")]
        res = self.collect(only=["a"])
        sent = []
        sender = [("fake", sent.append)]
        ids = res[0]["new_ids"]
        self.assertGreaterEqual(self.conn.execute("SELECT score FROM jobs").fetchone()[0], self.profile["notify"]["min_score"])
        self.assertEqual(notify.notify_new(self.conn, self.profile, ids, senders=sender), 1)
        self.assertEqual(notify.notify_new(self.conn, self.profile, ids, senders=sender), 0)
        self.assertEqual(len(sent), 1)

    def test_manual_discord_import(self):
        from vagas import manual

        res = manual.import_job(self.conn, self.profile,
                                url="https://discord.com/channels/1078753591264809070/1562678466132971570/1562678466132971570",
                                text="Long Form Gaming YouTube Editor for 677K Channel\n" + BODY)
        self.assertTrue(res["created"])
        self.assertEqual(res["job"]["published_at"], "2026-10-22T04:06:19Z")
        self.assertEqual(res["job"]["discovery_kind"], "manual")
        again = manual.import_job(self.conn, self.profile,
                                  url="https://discord.com/channels/1078753591264809070/1562678466132971570/1562678466132971570",
                                  text="Long Form Gaming YouTube Editor for 677K Channel\n" + BODY)
        self.assertFalse(again["created"])


class RedditParserTests(unittest.TestCase):
    def test_parse_fixture(self):
        items = reddit_rss.parse_feed((FIXTURES / "reddit_sample.rss").read_text(encoding="utf-8"))
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first["source_item_id"], "t3_abc123")
        self.assertEqual(first["community"], "r/HireAnEditor")
        self.assertEqual(first["author"], "u/creator_one")
        self.assertEqual(first["published_at"], "2026-10-27T06:45:00Z")
        self.assertNotIn("submitted by", first["description"])
        self.assertIn("$300", first["description"])
        self.assertEqual(dedupe.canonical_url(first["source_url"]), "reddit:abc123")

    def test_invalid_xml_raises_collect_error(self):
        with self.assertRaises(CollectError):
            reddit_rss.parse_feed("<html>blocked</html")


class CanonicalUrlTests(unittest.TestCase):
    def test_tracking_removed(self):
        self.assertEqual(dedupe.canonical_url("https://www.himalayas.app/jobs/x/?utm_source=a&ref=b&page=2#top"),
                         "https://himalayas.app/jobs/x?page=2")
        self.assertEqual(dedupe.canonical_url("https://old.reddit.com/r/a/comments/XyZ12/title/"), "reddit:xyz12")


if __name__ == "__main__":
    unittest.main()
