"""CEO task actions: native 3-dot menu, safe abandon, soft-remove and queue behavior."""
import os
import secrets
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from backend import office_db, meita_autowork, workforce
from backend.main import app


class TaskAbandonAndRemoveTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        self.assertEqual(self.client.post("/api/login",json={
            "password":os.environ["CEO_PASSWORD"]}).status_code,200)
        self.task_ids=[]
        self.job_ids=[]
        self.proposal_ids=[]
        self.run_ids=[]
        with office_db.connect() as db:
            worker=db.execute("""SELECT * FROM staff WHERE org_role='Director'
                AND department='Creative' AND status='Available' LIMIT 1""").fetchone()
            self.assertIsNotNone(worker)
            self.worker=dict(worker)

    def tearDown(self):
        with office_db.connect() as db:
            for run_id in self.run_ids:
                db.execute("DELETE FROM workforce_runs WHERE id=%s",(run_id,))
            for job_id in self.job_ids:
                db.execute("DELETE FROM ai_job_attempts WHERE job_id=%s",(job_id,))
                db.execute("DELETE FROM coo_jobs WHERE id=%s",(job_id,))
            for proposal_id in self.proposal_ids:
                db.execute("DELETE FROM coo_proposals WHERE id=%s",(proposal_id,))
            for task_id in self.task_ids:
                db.execute("DELETE FROM tasks WHERE id=%s",(task_id,))
        self.client.close()

    def task(self):
        r=self.client.post("/api/tasks",json={
            "title":"CEO Cancel Test Project","description":"Create a draft film brief.",
            "assignee":self.worker["id"],"priority":"Normal","recurrence":"None"})
        self.assertEqual(r.status_code,200,r.text)
        self.task_ids.append(r.json()["id"])
        return r.json()["id"]

    def meita_job(self):
        proposal_id="ci_cancel_"+secrets.token_hex(8)
        with office_db.connect() as db:
            meita_autowork.init(db)
            db.execute("""INSERT INTO coo_proposals (id,title,brief,plan,status)
                VALUES(%s,'CEO Controlled Task','Internal brief',%s,'Pending approval')""",
                (proposal_id,Jsonb({"title":"CEO Controlled Task","jobs":[{
                    "staff_id":self.worker["id"],"title":"CEO Agent Draft",
                    "brief":"Produce an internal concept for CEO review."}]})))
            made=meita_autowork.assign(db,proposal_id,[self.worker],automatic=True)
        self.proposal_ids.append(proposal_id)
        one=made["jobs"][0]
        self.job_ids.append(one["job_id"])
        self.task_ids.append(one["task_id"])
        return proposal_id,one["job_id"],one["task_id"]

    def test_manual_abandon_and_remove_keep_audit(self):
        task_id=self.task()
        response=self.client.post("/api/tasks/"+task_id+"/abandon")
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["status"],"Abandoned")
        self.assertEqual(self.client.post("/api/workforce/run/"+task_id).status_code,409)
        visible=self.client.get("/api/office").json()["tasks"]
        self.assertEqual(next(x for x in visible if x["id"]==task_id)["status"],"Abandoned")
        result=self.client.post("/api/tasks/"+task_id+"/remove")
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(any(x["id"]==task_id for x in self.client.get("/api/office").json()["tasks"]))
        with office_db.connect() as db:
            self.assertIsNotNone(db.execute("SELECT deleted_at FROM tasks WHERE id=%s",
                (task_id,)).fetchone()["deleted_at"])
            self.assertIsNotNone(db.execute("SELECT id FROM audit WHERE action='task.archived' AND detail LIKE %s",
                (task_id+"%",)).fetchone())

    def test_queued_meita_job_abandoned_is_not_claimed(self):
        project_id,job_id,task_id=self.meita_job()
        result=self.client.post("/api/meita/jobs/"+job_id+"/abandon")
        self.assertEqual(result.status_code,200,result.text)
        with office_db.connect() as db:
            job=db.execute("SELECT status FROM coo_jobs WHERE id=%s",(job_id,)).fetchone()
            task=db.execute("SELECT status FROM tasks WHERE id=%s",(task_id,)).fetchone()
        self.assertEqual(job["status"],"Cancelled")
        self.assertEqual(task["status"],"Abandoned")
        history=self.client.get("/api/meita/task-history").json()["requests"]
        proposal=next(p for p in history if p["id"]==project_id)
        self.assertEqual(proposal["jobs"][0]["status"],"Cancelled")
        self.assertEqual(self.client.post("/api/meita/jobs/"+job_id+"/retry").status_code,409)

    def test_running_cancel_then_archive_never_restores_task(self):
        project_id,job_id,task_id=self.meita_job()
        with office_db.connect() as db:
            db.execute("UPDATE coo_jobs SET status='Running' WHERE id=%s",(job_id,))
        cancel=self.client.post("/api/meita/jobs/"+job_id+"/abandon")
        self.assertEqual(cancel.status_code,200,cancel.text)
        self.assertTrue(cancel.json()["cancellation_pending"])
        with office_db.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM coo_jobs WHERE id=%s",
                (job_id,)).fetchone()["status"],"Cancellation requested")
        # A previously started provider call returning now must not publish.
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true","OPENAI_API_KEY":"CI-FAKE"}):
            with patch("backend.workforce.ai_provider.response",
                       return_value={"output":[{"type":"message","content":[{
                           "type":"output_text","text":"An internal cancelled report"}]}]}):
                with self.assertRaises(HTTPException) as ctx:
                    workforce.execute({"id":task_id,"title":"CEO cancelled test",
                                       "brief":"Test cancelled deliverable","category":"General"},
                                      self.worker)
        self.assertEqual(ctx.exception.status_code,409)
        removed=self.client.post("/api/tasks/"+task_id+"/remove")
        self.assertEqual(removed.status_code,200,removed.text)
        with office_db.connect() as db:
            db.execute("""UPDATE coo_jobs SET
                updated_at=NOW()-INTERVAL '30 minutes' WHERE id=%s""",(job_id,))
        meita_autowork.recover_interrupted(office_db.connect)
        with office_db.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM coo_jobs WHERE id=%s",
                (job_id,)).fetchone()["status"],"Cancelled")
            self.assertIsNotNone(db.execute("SELECT deleted_at FROM tasks WHERE id=%s",
                (task_id,)).fetchone()["deleted_at"])

    def test_remove_from_meita_hides_job_but_keeps_records(self):
        project_id,job_id,task_id=self.meita_job()
        res=self.client.post("/api/meita/jobs/"+job_id+"/remove")
        self.assertEqual(res.status_code,200,res.text)
        history=self.client.get("/api/meita/task-history").json()["requests"]
        self.assertFalse(any(p["id"]==project_id for p in history))
        live=self.client.get("/api/meita/live-activity").json()["jobs"]
        self.assertFalse(any(x["id"]==job_id for x in live))
        with office_db.connect() as db:
            job=db.execute("SELECT status,archived_at FROM coo_jobs WHERE id=%s",(job_id,)).fetchone()
            self.assertIsNotNone(job["archived_at"])
            self.assertEqual(job["status"],"Cancelled")

    def test_completed_task_can_be_archived_without_erasing_output(self):
        task_id=self.task()
        run_id="ci_run_"+secrets.token_hex(7)
        with office_db.connect() as db:
            workforce.init(db)
            db.execute("UPDATE tasks SET status='Completed',ai_run_started=TRUE WHERE id=%s",(task_id,))
            db.execute("""INSERT INTO workforce_runs
              (id,task_id,task_title,staff_id,staff_name,model,result,status)
              VALUES(%s,%s,'CEO Completed Test',%s,%s,'test','Preserved output','Approved')""",
              (run_id,task_id,self.worker["id"],self.worker["name"]))
        self.run_ids.append(run_id)
        self.assertEqual(self.client.post("/api/tasks/"+task_id+"/abandon").status_code,409)
        res=self.client.post("/api/tasks/"+task_id+"/remove")
        self.assertEqual(res.status_code,200,res.text)
        with office_db.connect() as db:
            stored=db.execute("SELECT status,result FROM workforce_runs WHERE id=%s",
                              (run_id,)).fetchone()
            self.assertEqual(stored["status"],"Approved")
            self.assertEqual(stored["result"],"Preserved output")

    def test_more_menu_appears_in_both_task_centre_and_meita(self):
        html=(Path(__file__).resolve().parents[1]/"frontend"/"index.html").read_text()
        self.assertIn('class="task-more"',html)
        self.assertIn("controlOfficeTask(",html)
        self.assertIn("controlMeitaJob(",html)
        self.assertIn("Remove from view",html)
        self.assertIn("Abandon task",html)
        self.assertIn("aria-label",html)
        self.assertIn("task-more-menu",html)
        anonymous=TestClient(app)
        self.assertEqual(anonymous.post("/api/tasks/unknown/abandon").status_code,401)
        self.assertEqual(anonymous.post("/api/meita/jobs/unknown/remove").status_code,401)
        anonymous.close()


if __name__=="__main__":
    unittest.main()
