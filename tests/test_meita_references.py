"""Meita attachment validation and aligned internal-work state regression tests."""
import io
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import references as refs, office_db, meita_autowork
from backend.main import app


class ReferenceHelpersTests(unittest.TestCase):
    def test_rejects_private_hosts(self):
        for url in ("https://127.0.0.1/admin", "http://example.com/",
                    "https://localhost/", "https://192.168.1.1/",
                    "https://user:secret@example.com/"):
            with self.subTest(url=url), self.assertRaises(HTTPException):
                refs.normalize_url(url)

    def test_valid_file_reference_plain_text(self):
        class Db:
            def __init__(self):self.args=None
            def execute(self, stmt, args):
                self.args=args
        db=Db()
        result=refs.save_file(db,"creative-brief.txt",b"Pastel pink and violet moodboard")
        self.assertEqual(result["kind"],"document")
        self.assertEqual(db.args[-2],"Pastel pink and violet moodboard")
        self.assertEqual(len(result["id"]),24)

    def test_oversize_and_unsupported_files(self):
        with self.assertRaises(HTTPException):
            refs.save_file(None,"dangerous.exe",b"abc")
        with self.assertRaises(HTTPException):
            refs.save_file(None,"huge.txt",b"a"*(refs.MAX_BYTES+1))


class MeitaWorkspaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        response=self.client.post("/api/login",json={
            "password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(response.status_code,200,response.text)

    def tearDown(self):
        self.client.close()

    def test_upload_and_reference_vault_access(self):
        response=self.client.post("/api/meita/references/upload",
            files={"file":("moodboard.txt",b"Keep violet and pastel pink colors","text/plain")})
        self.assertEqual(response.status_code,200,response.text)
        ref=response.json()
        self.assertEqual(ref["kind"],"document")
        with office_db.connect() as db:
            summary=refs.summaries(db,[ref["id"]])
            self.assertIn("violet and pastel pink",summary)
            self.assertEqual(refs.validate_ids(db,[ref["id"]])[0]["id"],ref["id"])
            db.execute("DELETE FROM meita_references WHERE id=%s",(ref["id"],))

    def test_uploaded_files_require_login(self):
        anonymous=TestClient(app)
        response=anonymous.post("/api/meita/references/upload",
            files={"file":("brief.txt",b"creative","text/plain")})
        self.assertEqual(response.status_code,401)
        anonymous.close()

    def test_live_activity_returns_actual_job_records(self):
        response=self.client.get("/api/meita/live-activity")
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsInstance(response.json()["jobs"],list)


if __name__=="__main__":
    unittest.main()
