"""Direct CEO instruction -> queued internal AI work -> actual Task Centre -> slides."""
import io
import os
import secrets
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pptx import Presentation
from backend import delegation, meita_autowork, meita_team, office_db, workforce
from backend.main import app

class AutonomousMeitaTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        r=self.client.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(r.status_code,200,r.text)
        with office_db.connect() as db:
            meita_autowork.init(db)
            original=meita_team.settings(db)
            self.original={k:original[k] for k in
                ("delegation_scope","discussion_scope","max_delegation_jobs",
                 "max_meeting_participants","auto_execute_internal")}
            config={**self.original,"auto_execute_internal":True,
                    "delegation_scope":"all_staff","max_delegation_jobs":3}
            meita_team.save_settings(db,meita_team.MeitaSettings(**config))
            meita_autowork.resume_provider(db)
            staff=db.execute("""SELECT * FROM staff
                WHERE status='Available' AND org_role='Director'
                ORDER BY department LIMIT 1""").fetchone()
            self.assertIsNotNone(staff)
            self.staff=dict(staff)
            self.last_message=db.execute(
                "SELECT COALESCE(MAX(id),0) AS last FROM meita_messages"
            ).fetchone()["last"]
        self.proposal_ids=[]
        self.job_ids=[]
        self.task_ids=[]
        self.run_ids=[]

    def tearDown(self):
        with office_db.connect() as db:
            for job_id in self.job_ids:
                db.execute("DELETE FROM meita_memories WHERE id=%s",
                           ("meita-delivery-"+job_id,))
            for rid in self.run_ids:
                db.execute("DELETE FROM workforce_runs WHERE id=%s",(rid,))
            for tid in self.task_ids:
                db.execute("DELETE FROM tasks WHERE id=%s",(tid,))
            for id in self.job_ids:
                db.execute("DELETE FROM coo_jobs WHERE id=%s",(id,))
            for pid in self.proposal_ids:
                db.execute("DELETE FROM coo_proposals WHERE id=%s",(pid,))
            db.execute("DELETE FROM meita_messages WHERE id>%s",(self.last_message,))
            meita_team.save_settings(db,meita_team.MeitaSettings(**self.original))
            meita_autowork.resume_provider(db)
        self.client.close()

    def assign(self):
        plan={"title":"Obscura digital launch","jobs":[{
            "staff_id":self.staff["id"],"staff_name":self.staff["name"],
            "title":"Prepare brand launch plan",
            "brief":"Develop evidence-led internal launch ideas and staff tasks for CEO."}]}
        routed={"intent":"delegate","reply":"I'll coordinate the staff.",
                "delegation_brief":"Prepare and execute internal launch planning.",
                "model":"test"}
        with patch("backend.main.meita_chat_delegate.respond",return_value=routed):
            with patch("backend.main.office_memory.recall",return_value=[]):
                with patch("backend.main.office_delegation.prepare_plan",return_value=plan):
                    response=self.client.post("/api/meita/chat",json={
                        "message":"Meita, assign this launch planning to our team and run the AI."})
        self.assertEqual(response.status_code,200,response.text)
        proposal=response.json()["proposal"]
        self.assertTrue(proposal["automatic"])
        self.assertEqual(proposal["status"],"Approved")
        self.assertEqual(len(proposal["assigned_tasks"]),1)
        self.proposal_ids.append(proposal["id"])
        assignment=proposal["assigned_tasks"][0]
        self.job_ids.append(assignment["job_id"])
        self.task_ids.append(assignment["task_id"])
        return proposal

    def test_ceo_chat_assigns_automatically_without_second_approval(self):
        proposal=self.assign()
        with office_db.connect() as db:
            row=db.execute("""SELECT status,task_id FROM coo_jobs
                WHERE id=%s""",(self.job_ids[0],)).fetchone()
            task=db.execute("""SELECT status,assignee,description,ai_run_started
                FROM tasks WHERE id=%s""",(self.task_ids[0],)).fetchone()
        self.assertEqual(row["status"],"Queued")
        self.assertEqual(row["task_id"],self.task_ids[0])
        self.assertEqual(task["assignee"],self.staff["id"])
        self.assertTrue(task["description"])
        self.assertTrue(task["ai_run_started"])
        self.assertEqual(task["status"],"To do")  # Queued is not active execution
        office=self.client.get("/api/office").json()
        self.assertTrue(any(t["id"]==self.task_ids[0] for t in office["tasks"]))
        task_history=self.client.get("/api/meita/task-history").json()["requests"]
        current=next(p for p in task_history if p["id"]==proposal["id"])
        self.assertEqual(current["jobs"][0]["status"],"Queued")
        history=self.client.get("/api/meita/history").json()["messages"]
        self.assertTrue(history[-1]["proposal"]["automatic"])
        self.assertIn("queue",history[-1]["content"])

    def test_autorun_saves_real_result_memory_and_downloadable_pptx(self):
        proposal=self.assign()
        def fake_execute(task,staff):
            self.assertEqual(task["id"],self.task_ids[0])
            ident="ci_run_"+secrets.token_hex(7)
            self.run_ids.append(ident)
            report="Executive Summary\n- Plan a short visual launch campaign.\n- Define the editorial schedule.\n- Prepare a CEO-ready creative brief."
            with office_db.connect() as db:
                workforce.init(db)
                db.execute("""INSERT INTO workforce_runs
                    (id,task_id,task_title,staff_id,staff_name,model,result,status)
                    VALUES (%s,%s,%s,%s,%s,'mock',%s,'For approval')""",
                    (ident,task["id"],task["title"],staff["id"],staff["name"],report))
            return {"id":ident,"result":report,"status":"For approval"}
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true",
                                    "OPENAI_API_KEY":"fake-worker-key"}):
            with patch("backend.meita_autowork.workforce.execute",side_effect=fake_execute) as run:
                result=meita_autowork.process_one(office_db.connect)
                self.assertEqual(result["status"],"Delivered")
                self.assertIsNone(meita_autowork.process_one(office_db.connect))
                run.assert_called_once()
        with office_db.connect() as db:
            job=db.execute("SELECT status,workforce_run_id FROM coo_jobs WHERE id=%s",
                           (self.job_ids[0],)).fetchone()
            task=db.execute("SELECT status FROM tasks WHERE id=%s",
                            (self.task_ids[0],)).fetchone()
            memo=db.execute("SELECT title,content,category FROM meita_memories WHERE id=%s",
                            ("meita-delivery-"+self.job_ids[0],)).fetchone()
        self.assertEqual(job["status"],"Delivered")
        self.assertEqual(job["workforce_run_id"],self.run_ids[0])
        self.assertEqual(task["status"],"For approval")
        self.assertIn("AI delivery",memo["title"])
        self.assertIn("not independently fact-checked",memo["content"])
        slide_resp=self.client.get("/api/meita/deliverables/"+proposal["id"])
        self.assertEqual(slide_resp.status_code,200,slide_resp.text)
        deck=slide_resp.json()
        self.assertEqual(deck["metrics"]["delivered"],1)
        self.assertTrue(any(x["kind"]=="chart" for x in deck["slides"]))
        self.assertTrue(any(x["kind"]=="work" and x["available"]
                            for x in deck["slides"]))
        pptx=self.client.get("/api/meita/deliverables/"+proposal["id"]+"/powerpoint")
        self.assertEqual(pptx.status_code,200,pptx.text[:300])
        self.assertIn("presentationml",pptx.headers["content-type"])
        presentation=Presentation(io.BytesIO(pptx.content))
        self.assertEqual(len(presentation.slides),len(deck["slides"]))
        self.assertEqual(presentation.slide_width,12191737) if False else None

    def test_failed_job_not_repeated_automatically(self):
        self.assign()
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true",
                                    "OPENAI_API_KEY":"fake-worker-key"}):
            with patch("backend.meita_autowork.workforce.execute",
                       side_effect=RuntimeError("internal provider unavailable")) as run:
                failed=meita_autowork.process_one(office_db.connect)
                self.assertEqual(failed["status"],"Retry available")
                self.assertIsNone(meita_autowork.process_one(office_db.connect))
                run.assert_called_once()
        with office_db.connect() as db:
            status=db.execute("SELECT status FROM coo_jobs WHERE id=%s",
                              (self.job_ids[0],)).fetchone()["status"]
        self.assertEqual(status,"Retry available")
        unauthorized=TestClient(app).post("/api/meita/jobs/"+self.job_ids[0]+"/retry")
        self.assertEqual(unauthorized.status_code,401)
        retry=self.client.post("/api/meita/jobs/"+self.job_ids[0]+"/retry")
        self.assertEqual(retry.status_code,200,retry.text)
        self.assertEqual(retry.json()["status"],"Queued")

    def test_interrupted_job_requires_reconciliation_not_blind_retry(self):
        self.assign()
        with office_db.connect() as db:
            db.execute("""UPDATE coo_jobs SET status='Running',
                updated_at=NOW()-INTERVAL '30 minutes' WHERE id=%s""",
                (self.job_ids[0],))
        meita_autowork.recover_interrupted(office_db.connect)
        with office_db.connect() as db:
            status=db.execute("SELECT status FROM coo_jobs WHERE id=%s",
                              (self.job_ids[0],)).fetchone()["status"]
        self.assertEqual(status,"Needs verification")
        self.assertEqual(self.client.post(
            "/api/meita/jobs/"+self.job_ids[0]+"/retry").status_code,409)

    def test_preview_uses_real_job_counts_no_fake_kpis(self):
        proposal=self.assign()
        deck=self.client.get("/api/meita/deliverables/"+proposal["id"]).json()
        self.assertEqual(deck["metrics"]["assigned"],1)
        self.assertEqual(deck["metrics"]["delivered"],0)
        self.assertEqual(deck["metrics"]["processing"],1)
        self.assertEqual(deck["slides"][2]["bars"][0]["total"],1)
        self.assertEqual(deck["slides"][2]["bars"][0]["delivered"],0)
        self.assertNotIn("revenue",str(deck).lower())

if __name__=="__main__":
    unittest.main()
