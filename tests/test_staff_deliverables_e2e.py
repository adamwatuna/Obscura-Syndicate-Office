"""Full API data flow: CEO task -> real stored AI output (mocked provider)
-> central submissions -> actual media files -> CEO approval -> task completion.
No production data or billable AI requests.
"""
import io
import os
import secrets
import unittest
from unittest.mock import patch
from PIL import Image
from pptx import Presentation
from fastapi.testclient import TestClient

from backend import office_db
from backend.main import app


class StaffDeliveryInboxTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        response=self.client.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(response.status_code,200,response.text)
        with office_db.connect() as db:
            staff=db.execute("""SELECT id,name,role,department FROM staff
                 WHERE status='Available' AND org_role='Specialist' AND department='Creative' ORDER BY id LIMIT 1""").fetchone()
            self.assertIsNotNone(staff)
            self.person=dict(staff)
        self.tasks=[]
        self.runs=[]
        self.assets=[]

    def tearDown(self):
        with office_db.connect() as db:
            for run in self.runs:
                db.execute("DELETE FROM workforce_assets WHERE run_id=%s",(run,))
                db.execute("DELETE FROM workforce_runs WHERE id=%s",(run,))
            for task in self.tasks:
                db.execute("DELETE FROM tasks WHERE id=%s",(task,))
        self.client.close()

    def _staff_submission(self):
        task=self.client.post("/api/tasks",json={"title":"Private Office E2E storyboard",
             "description":"Draft an original executive storyboard and editorial outline",
             "assignee":self.person["id"],"priority":"Normal","recurrence":"None"})
        self.assertEqual(task.status_code,200,task.text)
        task_id=task.json()["id"]
        self.tasks.append(task_id)
        response={"output":[{"type":"message","content":[
            {"type":"output_text","text":"Executive storyboard\\nScene 1: Office team planning.\\nScene 2: CEO review and approval."}]}]}
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true","OPENAI_API_KEY":"CI-FAKE-KEY"}):
            with patch("backend.workforce.ai_provider.response",return_value=response):
                generated=self.client.post("/api/workforce/run/"+task_id)
        self.assertEqual(generated.status_code,200,generated.text)
        run=generated.json()["id"]
        self.runs.append(run)
        return task_id,run

    def test_full_task_to_specialist_submission_review_and_download(self):
        task_id,run_id=self._staff_submission()
        inbox=self.client.get("/api/deliverables")
        self.assertEqual(inbox.status_code,200,inbox.text)
        row=next(item for item in inbox.json()["items"] if item["id"]==run_id)
        self.assertEqual(row["status"],"For approval")
        self.assertEqual(row["task_id"],task_id)
        self.assertEqual(row["staff_role"],self.person["role"])
        self.assertEqual(row["department"],self.person["department"])
        self.assertEqual(row["assets"],[])
        detail=self.client.get("/api/deliverables/"+run_id)
        self.assertEqual(detail.status_code,200,detail.text)
        self.assertIn("Executive storyboard",detail.json()["result"])

        txt=self.client.get("/api/deliverables/"+run_id+"/report.txt")
        self.assertEqual(txt.status_code,200)
        self.assertIn("Executive storyboard",txt.text)
        self.assertIn("attachment",txt.headers["content-disposition"])

        slides=self.client.get("/api/deliverables/"+run_id+"/presentation.pptx")
        self.assertEqual(slides.status_code,200,slides.text[:140])
        presentation=Presentation(io.BytesIO(slides.content))
        self.assertGreaterEqual(len(presentation.slides),2)
        self.assertIn("presentationml",slides.headers["content-type"])

        output=io.BytesIO()
        Image.new("RGB",(32,32),"purple").save(output,format="PNG")
        upload=self.client.post("/api/deliverables/"+run_id+"/files",
            files={"file":("storyboard.png",output.getvalue(),"image/png")})
        self.assertEqual(upload.status_code,200,upload.text)
        asset=upload.json()["id"]
        self.assets.append(asset)
        loaded=self.client.get("/api/deliverables")
        new=next(item for item in loaded.json()["items"] if item["id"]==run_id)
        self.assertEqual(len(new["assets"]),1)
        self.assertEqual(new["assets"][0]["kind"],"image")

        download=self.client.get("/api/deliverables/"+run_id+"/files/"+asset)
        self.assertEqual(download.status_code,200)
        self.assertEqual(download.content,output.getvalue())
        self.assertIn("attachment",download.headers["content-disposition"])
        preview=self.client.get("/api/deliverables/"+run_id+"/files/"+asset+"?preview=true")
        self.assertEqual(preview.status_code,200)
        self.assertEqual(preview.content,output.getvalue())
        self.assertEqual(preview.headers["content-disposition"],"inline")
        self.assertEqual(preview.headers["cache-control"],"private, no-store")

        approval=self.client.post("/api/workforce/runs/"+run_id+"/review",
            json={"status":"Approved"})
        self.assertEqual(approval.status_code,200,approval.text)
        task=self.client.get("/api/office").json()["tasks"]
        saved=next(t for t in task if t["id"]==task_id)
        self.assertEqual(saved["status"],"Completed")
        status=self.client.get("/api/deliverables").json()["items"]
        self.assertEqual(next(r for r in status if r["id"]==run_id)["status"],"Approved")

    def test_files_are_real_and_authenticated_not_fabricated(self):
        task_id,run_id=self._staff_submission()
        anon=TestClient(app)
        self.assertEqual(anon.get("/api/deliverables").status_code,401)
        self.assertEqual(anon.get("/api/deliverables/"+run_id).status_code,401)
        self.assertEqual(anon.get("/api/deliverables/"+run_id+"/report.txt").status_code,401)
        self.assertEqual(anon.post("/api/deliverables/"+run_id+"/files",
            files={"file":("fake.png",b"fake","image/png")}).status_code,401)
        anon.close()
        bad=self.client.post("/api/deliverables/"+run_id+"/files",
            files={"file":("fake.png",b"not-a-png","image/png")})
        self.assertEqual(bad.status_code,400,bad.text)
        mismatch=self.client.post("/api/deliverables/"+run_id+"/files",
            files={"file":("evil.exe",b"MZ","application/octet-stream")})
        self.assertEqual(mismatch.status_code,400,mismatch.text)
        with office_db.connect() as db:
            self.assertEqual(db.execute("""SELECT COUNT(*) AS n FROM workforce_assets
                WHERE run_id=%s""",(run_id,)).fetchone()["n"],0)

    def test_submitted_work_column_not_another_mock_report_feed(self):
        source=(__import__("pathlib").Path(__file__).resolve().parents[1]/"frontend"/"index.html").read_text()
        for key in ('data-view="deliverables"','id="deliveryInbox"','id="deliveryDetail"',
                    'id="deliveryFilter"','id="deliverySearch"','id="deliveryCountApproved"',
                    'Download presentation (.pptx)','Download original file',
                    "async function loadDeliveryInbox()"):
            self.assertIn(key,source)
        self.assertIn("No photo, video or source file has been submitted",source)


if __name__=="__main__":
    unittest.main()
