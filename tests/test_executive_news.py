"""End-to-end news briefing task, sourced web search and CEO dashboard checks."""
import io
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import office_db, news_briefings, task_schedule, workforce
from backend.main import app

EXAMPLE_SOURCE="https://example.gov.my/official-news-brief"
EXAMPLE_RESULT={
    "output":[
        {"type":"web_search_call",
         "action":{"type":"search","sources":[
             {"url":EXAMPLE_SOURCE,"title":"Official press release"}]}},
        {"type":"message","content":[
            {"type":"output_text","text":"Verified update dated 2026-11-13 with source.",
             "annotations":[{"type":"url_citation","url":EXAMPLE_SOURCE,
                             "title":"Official press release"}]}
        ]}
    ]
}

class NewsBriefingTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        login=self.client.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(login.status_code,200,login.text)
        with office_db.connect() as db:
            staff=db.execute("""SELECT id FROM staff
                WHERE org_role='Director' AND department='Research & Intelligence'
                LIMIT 1""").fetchone()
            self.assertIsNotNone(staff)
            self.assignee=staff["id"]
        self.ids=[]
        self.runs=[]
        self.series=[]

    def tearDown(self):
        with office_db.connect() as db:
            workforce.init(db)
            for run_id in self.runs:
                db.execute("DELETE FROM workforce_runs WHERE id=%s",(run_id,))
            for sid in self.series:
                db.execute("DELETE FROM tasks WHERE series_id=%s",(sid,))
                db.execute("DELETE FROM task_series WHERE id=%s",(sid,))
            for ident in self.ids:
                db.execute("DELETE FROM tasks WHERE id=%s",(ident,))
        self.client.close()

    def make_task(self,category="News Update",recurrence="None"):
        body={
            "title":"Latest policy news briefing",
            "description":"Find developments in Malaysian policy from the last seven days; cite dates.",
            "assignee":self.assignee,
            "category":category,"priority":"Normal",
            "due_date":(task_schedule.local_today()+timedelta(days=1)).isoformat(),
            "recurrence":recurrence}
        res=self.client.post("/api/tasks",json=body)
        self.assertEqual(res.status_code,200,res.text)
        ident=res.json()["id"]
        self.ids.append(ident)
        if res.json().get("series_id"):self.series.append(res.json()["series_id"])
        return ident,body

    def test_news_category_and_recurring_task_are_persisted(self):
        task_id,body=self.make_task(recurrence="Daily")
        listing=self.client.get("/api/office").json()["tasks"]
        entry=next(x for x in listing if x["id"]==task_id)
        self.assertEqual(entry["category"],"News Update")
        updated={**body,"title":"New updated news title"}
        self.assertEqual(self.client.put("/api/tasks/"+task_id,json=updated).status_code,200)
        with office_db.connect() as db:
            series=db.execute("SELECT category FROM task_series WHERE id=%s",
                              (self.series[0],)).fetchone()
            self.assertEqual(series["category"],"News Update")
            task_schedule.generate_due(db,task_schedule.local_today()+timedelta(days=2))
            occurrences=db.execute("""SELECT category FROM tasks
                WHERE series_id=%s AND id<>%s""",
                (self.series[0],task_id)).fetchall()
        self.assertTrue(occurrences)
        self.assertTrue(all(x["category"]=="News Update" for x in occurrences))

    def test_web_search_briefing_appears_in_hq_and_can_be_approved(self):
        ident,_=self.make_task()
        request_payloads=[]
        def fake_urlopen(request,timeout=None):
            request_payloads.append(json.loads(request.data.decode()))
            return io.BytesIO(json.dumps(EXAMPLE_RESULT).encode())
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true",
                                    "OPENAI_API_KEY":"fake-ci-key"}):
            with patch("backend.workforce.urllib.request.urlopen",side_effect=fake_urlopen):
                run=self.client.post("/api/workforce/run/"+ident)
        self.assertEqual(run.status_code,200,run.text)
        self.runs.append(run.json()["id"])
        self.assertEqual(request_payloads[0]["tool_choice"],"required")
        self.assertEqual(request_payloads[0]["tools"][0]["type"],"web_search")
        response=self.client.get("/api/news-briefings")
        self.assertEqual(response.status_code,200,response.text)
        feed=response.json()["briefings"]
        current=next(x for x in feed if x["task_id"]==ident)
        self.assertEqual(current["status"],"For approval")
        self.assertTrue(current["web_searched"])
        self.assertEqual(current["sources"][0]["url"],EXAMPLE_SOURCE)
        self.assertTrue(current["searched_at"])
        reviewed=self.client.post("/api/workforce/runs/"+self.runs[0]+"/review",
                                  json={"status":"Approved"})
        self.assertEqual(reviewed.status_code,200,reviewed.text)
        approved=self.client.get("/api/news-briefings").json()["briefings"]
        self.assertEqual(next(x for x in approved if x["task_id"]==ident)["status"],"Approved")

    def test_news_label_without_web_sources_not_published(self):
        task_id,_=self.make_task()
        unavailable={"output":[{"type":"message",
            "content":[{"type":"output_text","text":"Unverified news claimed here."}]}]}
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true",
                                    "OPENAI_API_KEY":"fake-ci-key"}):
            with patch("backend.workforce.urllib.request.urlopen",
                       return_value=io.BytesIO(json.dumps(unavailable).encode())):
                run=self.client.post("/api/workforce/run/"+task_id)
        self.assertEqual(run.status_code,502,run.text)
        with office_db.connect() as db:
            row=db.execute("SELECT ai_run_started,status FROM tasks WHERE id=%s",
                           (task_id,)).fetchone()
            self.assertFalse(row["ai_run_started"])
            self.assertEqual(row["status"],"To do")
        self.assertFalse(any(x["task_id"]==task_id
            for x in self.client.get("/api/news-briefings").json()["briefings"]))

    def test_general_tasks_do_not_appear_in_news(self):
        ident,_=self.make_task(category="General")
        with office_db.connect() as db:
            workforce.init(db)
            run_id="newsfeed_ci_"+ident
            self.runs.append(run_id)
            db.execute("""INSERT INTO workforce_runs
                (id,task_id,task_title,staff_id,staff_name,model,result,
                 status,web_searched,sources)
                VALUES (%s,%s,'Ordinary report',%s,'Staff','test','Mock',
                'Approved',TRUE,%s::jsonb)""",
                (run_id,ident,self.assignee,json.dumps([{"url":EXAMPLE_SOURCE}])))
        items=self.client.get("/api/news-briefings").json()["briefings"]
        self.assertFalse(any(x["task_id"]==ident for x in items))

    def test_url_validation_for_source_links(self):
        source=workforce._news_sources({"output":[{"type":"message","content":[{
            "type":"output_text","annotations":[
                {"type":"url_citation","url":"javascript:alert(1)","title":"Danger"},
                {"type":"url_citation","url":EXAMPLE_SOURCE,"title":"News"}]}]}]})
        self.assertEqual(source,[{"url":EXAMPLE_SOURCE,"title":"News"}])
        self.assertEqual(TestClient(app).get("/api/news-briefings").status_code,401)

if __name__=="__main__":
    unittest.main()
