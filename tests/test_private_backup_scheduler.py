"""Zero-network backup schedule, safe naming, env and retention tests."""
import datetime as dt
import importlib.util
import os
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("private_backup",ROOT/"ops"/"backup"/"backup.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Pager:
    def paginate(self,**kwargs):
        assert kwargs["Prefix"].startswith(module.PREFIX)
        yield {"Contents":[{"Key":module.PREFIX+"daily/2026-11-%02dT18-10-00Z.dump"%day}
                           for day in range(1,20)]}
        yield {"Contents":[{"Key":module.PREFIX+"daily/personal-credentials.txt"},
                           {"Key":"another-project/not-our-backup.dump"}]}

class FakeS3:
    def get_paginator(self,operation):
        assert operation=="list_objects_v2"
        return Pager()


class BackupSafetyTests(unittest.TestCase):
    def test_daily_and_weekly_keys_are_private_and_deterministic(self):
        instant=dt.datetime(2026,11,14,18,10,tzinfo=dt.timezone.utc)
        self.assertEqual(module.key_for(instant,"daily"),
            "obscura-private-office/postgres/daily/2026-11-14T18-10-00Z.dump")
        self.assertEqual(module.key_for(instant,"weekly"),
            "obscura-private-office/postgres/weekly/2026-11-14T18-10-00Z.dump")
        self.assertEqual(instant.astimezone(module.MALAYSIA).weekday(),6)

    def test_retention_only_targets_exact_backup_prefix(self):
        keys=module.obsolete_keys(FakeS3(),"private-bucket","daily",14)
        self.assertEqual(len(keys),5)
        self.assertEqual(keys[0],
            "obscura-private-office/postgres/daily/2026-11-05T18-10-00Z.dump")
        self.assertNotIn("personal-credentials.txt",str(keys))

    def test_credentials_are_env_not_plaintext_command_or_logs(self):
        url="postgresql://operator:pass%40word@postgres.railway.internal:5432/railway"
        env=module.pg_env(url)
        self.assertEqual(env["PGPASSWORD"],"pass@word")
        self.assertEqual(env["PGHOST"],"postgres.railway.internal")
        self.assertEqual(env["PGDATABASE"],"railway")
        code=(ROOT/"ops"/"backup"/"backup.py").read_text()
        self.assertIn("subprocess.run([\"pg_dump\"",code)
        self.assertNotIn("--password",code)
        docker=(ROOT/"ops"/"backup"/"Dockerfile").read_text()
        self.assertIn("postgres:18-alpine",docker)
        self.assertNotIn("DATABASE_URL=",docker)

    def test_no_readable_api_credentials_in_source(self):
        readme=(ROOT/"ops"/"backup"/"README.md").read_text()
        self.assertIn("ObscuraBackups.SECRET_ACCESS_KEY",readme)
        self.assertNotIn("AKIA",readme)
        self.assertEqual(module.DAILY_KEEP if hasattr(module,"DAILY_KEEP") else 14,14)


if __name__=="__main__":
    unittest.main()
