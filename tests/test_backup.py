import shutil
import tempfile
import unittest
from pathlib import Path

from vagas import backup, db


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "vagas.db"
        conn = db.connect(self.db_path)
        conn.execute("INSERT INTO jobs(source_id, source_item_id, title, discovered_at) VALUES ('s', '1', 'antes', '2026-01-01T00:00:00Z')")
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_restore_roundtrip(self):
        dest = backup.backup(self.db_path, self.tmp / "backups")
        ok, info = backup.verify(dest)
        self.assertTrue(ok, info)
        self.assertEqual(list((self.tmp / "backups").iterdir()), [dest])  # arquivo único

        conn = db.connect(self.db_path)
        conn.execute("UPDATE jobs SET title='depois'")
        conn.commit()
        conn.close()

        safety = backup.restore(dest, self.db_path, self.tmp / "backups")
        self.assertTrue(safety.exists())
        conn = db.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT title FROM jobs").fetchone()[0], "antes")
        conn.close()

    def test_restore_rejects_invalid_file(self):
        bad = self.tmp / "ruim.db"
        bad.write_bytes(b"isto nao e um banco")
        with self.assertRaises(ValueError):
            backup.restore(bad, self.db_path, self.tmp / "backups")


if __name__ == "__main__":
    unittest.main()
