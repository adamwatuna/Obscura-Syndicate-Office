"""Launch readiness regressions: safe provider diagnostics, queue control,
login protections, real data charts and independent staff reporting."""
import io
import json
import os
import socket
import secrets
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from backend import ai_provider, meita_autowork, office_db
from backend.main import app


class PrivateOfficeLaunchReadiness(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        self.client_id="launch_"+secrets.token_hex(7)
        with office_db.connect() as db:
            meita_autowork.resume_provider(db)
        res=self.client.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(res.status_code,200,res.text)

    def tearDown(self):
        with office_db.connect() as db:
            db.execute("DELETE FROM coo_jobs WHERE id=%s",(self.client_id,))
            db.execute("DELETE FROM coo_proposals WHERE id=%s",(self.client_id,))
            db.execute("DELETE FROM ceo_login_attempts")
            meita_autowork.resume_provider(db)
        self.client.close()

    def test_authenticated_diagnostics_no_private_credentials(self):
        res=self.client.get("/api/ai/diagnostics")
        self.assertEqual(res.status_code,200,res.text)
        data=res.json()
        self.assertIn("key_present",data)
        self.assertIn("provider",data)
        self.assertNotIn("OPENAI_API_KEY",str(data))
        self.assertNotIn("DATABASE_URL",str(data))
        self.assertNotIn("SESSION_SECRET",str(data))
        anon=TestClient(app)
        self.assertEqual(anon.get("/api/ai/diagnostics").status_code,401)
        self.assertEqual(anon.post("/api/ai/diagnostics/resume").status_code,401)
        anon.close()

    def test_liveness_readiness_and_headers(self):
        anon=TestClient(app)
        self.assertEqual(anon.get("/api/health/live").status_code,200)
        self.assertEqual(anon.get("/api/health/ready").status_code,200)
        response=anon.get("/api/me")
        self.assertEqual(response.headers.get("x-frame-options"),"DENY")
        self.assertEqual(response.headers.get("x-content-type-options"),"nosniff")
        self.assertEqual(response.headers.get("cache-control"),"no-store")
        anon.close()

    def test_mutations_reject_cross_site_browser_origin(self):
        evil=self.client.post("/api/login",
            headers={"Origin":"https://untrusted.example"},
            json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(evil.status_code,403)
        wrong_fetch=self.client.post("/api/ai/diagnostics/resume",
            headers={"Sec-Fetch-Site":"cross-site"})
        self.assertEqual(wrong_fetch.status_code,403)

    def test_ceo_login_failure_throttling(self):
        visitor=TestClient(app)
        for _ in range(12):
            wrong=visitor.post("/api/login",json={"password":"incorrect-value"})
            self.assertIn(wrong.status_code,(401,429))
        self.assertEqual(visitor.post("/api/login",json={
            "password":os.environ["CEO_PASSWORD"]}).status_code,429)
        visitor.close()

    def test_provider_quota_error_is_safe_and_actionable(self):
        payload={"model":"test","input":"test"}
        body={"error":{"code":"insufficient_quota","message":"INTERNAL-SECRET-MUST-NOT-LEAK"}}
        error=urllib.error.HTTPError("https://api.openai.com/v1/responses",429,"Too Many",
            {"x-request-id":"req_safe_1234567"},io.BytesIO(json.dumps(body).encode()))
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true","OPENAI_API_KEY":"fake-unit-key"}):
            with patch("backend.ai_provider.urllib.request.urlopen",side_effect=error):
                with self.assertRaises(ai_provider.AIProviderError) as ctx:
                    ai_provider.response(payload,operation="unit",timeout=10)
        exc=ctx.exception
        self.assertEqual(exc.code,"insufficient_quota")
        self.assertEqual(exc.provider_status,429)
        self.assertEqual(exc.request_id,"req_safe_1234567")
        self.assertIn("billing",exc.detail)
        self.assertNotIn("INTERNAL-SECRET",exc.detail)

    def test_timeout_is_uncertain_and_not_auto_retried(self):
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true","OPENAI_API_KEY":"fake-unit-key"}):
            with patch("backend.ai_provider.urllib.request.urlopen",
                side_effect=socket.timeout("fake timeout")) as call:
                with self.assertRaises(ai_provider.AIProviderError) as ctx:
                    ai_provider.response({"model":"test","input":"hello"},timeout=10)
        self.assertTrue(ctx.exception.uncertain)
        self.assertEqual(ctx.exception.code,"timeout")
        self.assertEqual(call.call_count,1)

    def test_key_and_model_check_does_not_generate(self):
        with patch("backend.main.ai_provider.check_model",return_value={
            "connected":True,"status":"model_visible","model":"test-model",
            "advice":"Model accessible; test generation separately"}):
            check=self.client.get("/api/ai/diagnostics/connection")
        self.assertEqual(check.status_code,200)
        self.assertEqual(check.json()["status"],"model_visible")

    def test_provider_failure_pauses_queue_until_ceo_resumes(self):
        with office_db.connect() as db:
            db.execute("""INSERT INTO coo_proposals(id,title,brief,plan,status)
                VALUES(%s,'Launch CI','Test safe retry',%s,'Approved')""",
                (self.client_id,Jsonb({"title":"CI","jobs":[]})))
            db.execute("""INSERT INTO coo_jobs(id,proposal_id,title,brief,staff,status)
                VALUES(%s,%s,'Safe CI job','Do not call real AI',%s,'Queued')""",
                (self.client_id,self.client_id,Jsonb({
                    "id":"missing","name":"Fake","role":"QA","department":"Test"})))
            err=ai_provider.AIProviderError(503,"Quota is exhausted",
                "insufficient_quota",provider_status=429)
            meita_autowork._record_problem(db,err)
            self.assertTrue(meita_autowork.provider_status(db)["manual_resume_required"])
            self.assertIsNone(meita_autowork.claim(db))
        resume=self.client.post("/api/ai/diagnostics/resume")
        self.assertEqual(resume.status_code,200,resume.text)
        with office_db.connect() as db:
            job=meita_autowork.claim(db)
            self.assertEqual(job["id"],self.client_id)
            self.assertEqual(job["status"],"Running")
            self.assertEqual(job["attempts"],1)

    def test_charts_use_real_task_state_and_workforce_data(self):
        source=(Path(__file__).resolve().parents[1]/"frontend"/"index.html").read_text()
        for label in ("hqTaskDonut","hqTaskLegend","hqDepartmentBars",
                      "renderOfficeCharts(s,t)","renderLeadershipSpotlight(s)",
                      "workforce-director","workforce-specialist-grid",
                      "Reports to Meita","ai/diagnostics"):
            self.assertIn(label,source)
        self.assertIn("tasks.filter(t=>t.status==='Completed')",source)
        self.assertIn("staff.filter(x=>x.department===name&&x.org_role==='Specialist')",source)
        self.assertIn("option.textContent=person.role+",source)


if __name__=="__main__":
    unittest.main()
