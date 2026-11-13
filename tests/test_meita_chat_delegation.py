"""Meita conversational delegation: CEO request -> inline proposal -> explicit approval."""
import io
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import office_db, delegation, meita_team
from backend.main import app

def simulated_response(intent,reply,brief=""):
    body={"intent":intent,"reply":reply,"delegation_brief":brief}
    return io.BytesIO(json.dumps({"output":[
        {"type":"message","content":[
            {"type":"output_text","text":json.dumps(body)}]}]}).encode())

class MeitaChatDelegationTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        login=self.client.post("/api/login",json={
            "password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(login.status_code,200,login.text)
        with office_db.connect() as db:
            delegation.init(db)
            current=meita_team.settings(db)
            self.config={key:current[key] for key in (
                "delegation_scope","discussion_scope","max_delegation_jobs",
                "max_meeting_participants")}
            self.base=db.execute(
                "SELECT COALESCE(MAX(id),0) AS last FROM meita_messages"
            ).fetchone()["last"]
            self.base_proposals=db.execute(
                "SELECT COUNT(*) AS n FROM coo_proposals").fetchone()["n"]
            director=db.execute("""SELECT * FROM staff
                WHERE org_role='Director' AND status='Available'
                ORDER BY name LIMIT 1""").fetchone()
            self.assertIsNotNone(director)
            self.director=dict(director)

    def tearDown(self):
        with office_db.connect() as db:
            db.execute("""DELETE FROM coo_jobs WHERE proposal_id IN
                (SELECT id FROM coo_proposals WHERE chat_message_id>%s)""",
                (self.base,))
            db.execute("DELETE FROM coo_proposals WHERE chat_message_id>%s",
                       (self.base,))
            db.execute("DELETE FROM meita_messages WHERE id>%s",(self.base,))
            meita_team.save_settings(db,meita_team.MeitaSettings(**self.config))
        self.client.close()

    def send(self,mode,reply,brief="",text="Meita, delegate this campaign to the team"):
        with patch.dict(os.environ,{
            "AI_ENGINE_ENABLED":"true","OPENAI_API_KEY":"fake-chat-test-key"}):
            with patch("backend.main.office_memory.recall",return_value=[]):
                with patch("backend.meita_chat_delegate.urllib.request.urlopen",
                    return_value=simulated_response(mode,reply,brief)) as openai:
                    result=self.client.post("/api/meita/chat",json={"message":text})
        self.assertEqual(openai.call_count,1)
        request=json.loads(openai.call_args.args[0].data)
        self.assertEqual(request["text"]["format"]["type"],"json_schema")
        self.assertEqual(request["store"],False)
        return result

    def _staff_plan(self):
        return {"title":"Launch campaign responsibilities","jobs":[
            {"staff_id":self.director["id"],
             "staff_name":self.director["name"],
             "title":"Draft launch plan",
             "brief":"Write five channel launch ideas for the CEO's review."}]}

    def test_explicit_assign_chat_creates_pending_proposal_and_card_in_history(self):
        brief="Coordinate the next Obscura campaign across our AI team."
        with patch("backend.main.office_delegation.prepare_plan",
                   return_value=self._staff_plan()) as plan:
            result=self.send("delegate",
                "I have prepared an internal task proposal for your approval.",brief)
        self.assertEqual(result.status_code,200,result.text)
        proposal=result.json()["proposal"]
        self.assertEqual(proposal["status"],"Pending approval")
        self.assertEqual(proposal["plan"]["jobs"][0]["staff_id"],self.director["id"])
        self.assertEqual(plan.call_args.kwargs["max_jobs"],self.config["max_delegation_jobs"])
        with office_db.connect() as db:
            record=db.execute("""SELECT p.chat_message_id,p.status,
                (SELECT COUNT(*) FROM coo_jobs j WHERE j.proposal_id=p.id) AS job_count
                FROM coo_proposals p WHERE p.id=%s""",(proposal["id"],)).fetchone()
            self.assertEqual(record["job_count"],0)
            self.assertIsNotNone(record["chat_message_id"])
        history=self.client.get("/api/meita/history").json()["messages"]
        latest=history[-1]
        self.assertEqual(latest["role"],"assistant")
        self.assertEqual(latest["proposal"]["id"],proposal["id"])
        self.assertEqual(latest["proposal"]["status"],"Pending approval")

        approval=self.client.post(
            "/api/delegation/proposals/"+proposal["id"]+"/approve")
        self.assertEqual(approval.status_code,200,approval.text)
        self.assertEqual(approval.json()["status"],"Approved")
        history=self.client.get("/api/meita/history").json()["messages"]
        self.assertEqual(history[-1]["proposal"]["status"],"Approved")
        with office_db.connect() as db:
            jobs=db.execute("""SELECT id,status FROM coo_jobs
                WHERE proposal_id=%s""",(proposal["id"],)).fetchall()
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]["status"],"Approved")
        self.assertEqual(self.client.post(
            "/api/delegation/proposals/"+proposal["id"]+"/approve").status_code,409)

    def test_reject_from_chat_never_assigns_job(self):
        with patch("backend.main.office_delegation.prepare_plan",
                   return_value=self._staff_plan()):
            result=self.send("delegate",
                "Draft assignments are ready for your approval.",
                "Prepare a launch plan with the team.")
        self.assertEqual(result.status_code,200,result.text)
        proposal_id=result.json()["proposal"]["id"]
        reject=self.client.post(
            "/api/delegation/proposals/"+proposal_id+"/reject")
        self.assertEqual(reject.status_code,200,reject.text)
        self.assertEqual(self.client.get("/api/meita/history").json()[
            "messages"][-1]["proposal"]["status"],"Rejected")
        with office_db.connect() as db:
            jobs=db.execute("SELECT id FROM coo_jobs WHERE proposal_id=%s",
                            (proposal_id,)).fetchall()
        self.assertEqual(jobs,[])

    def test_regular_chat_is_only_a_reply_and_does_not_create_proposal(self):
        with patch("backend.main.office_delegation.prepare_plan") as planner:
            response=self.send("chat","I can help you prioritize, CEO.",
                text="Hi Meita, what do you think of our launch strategy?")
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()["proposal"])
        planner.assert_not_called()
        with office_db.connect() as db:
            proposals=db.execute("SELECT COUNT(*) AS n FROM coo_proposals").fetchone()["n"]
        self.assertEqual(proposals,self.base_proposals)
        self.assertNotIn("proposal",
            self.client.get("/api/meita/history").json()["messages"][-1])

    def test_plan_failure_does_not_persist_false_claim_or_chat_proposal(self):
        from fastapi import HTTPException
        with patch("backend.main.office_delegation.prepare_plan",
                   side_effect=HTTPException(502,"AI plan failed")):
            result=self.send("delegate",
                "A draft is prepared.", "Prepare a launch plan.")
        self.assertEqual(result.status_code,502)
        history=self.client.get("/api/meita/history").json()["messages"]
        self.assertFalse(any(m["id"]>self.base for m in history))
        with office_db.connect() as db:
            plans=db.execute("SELECT COUNT(*) AS n FROM coo_proposals").fetchone()["n"]
        self.assertEqual(plans,self.base_proposals)

    def test_director_scope_applies_to_chat_proposal_and_approval(self):
        with office_db.connect() as db:
            meita_team.save_settings(db,meita_team.MeitaSettings(
                **{**self.config,"delegation_scope":"directors_only",
                   "max_delegation_jobs":2}))
        with patch("backend.main.office_delegation.prepare_plan",
                   return_value=self._staff_plan()) as plan:
            result=self.send("delegate","Pending for CEO approval.",
                "Delegate internal campaign work to the Director.")
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(plan.call_args.kwargs["max_jobs"],2)
        assigned=plan.call_args.args[1]
        self.assertTrue(all(p["org_role"]=="Director" for p in assigned))
        self.assertEqual(self.client.post(
            "/api/delegation/proposals/"+result.json()["proposal"]["id"]+
            "/approve").status_code,200)

    def test_model_output_schema_rejects_incorrect_or_ambiguous_intents(self):
        from fastapi import HTTPException
        from backend import meita_chat_delegate
        bad={"output":[{"type":"message","content":[
            {"type":"output_text","text":json.dumps({
                "intent":"approve","reply":"Approved all work.",
                "delegation_brief":"Do it"})}]}]}
        with self.assertRaises(HTTPException):
            meita_chat_delegate.parse_response(bad)
        no_brief={"output":[{"type":"message","content":[
            {"type":"output_text","text":json.dumps({
                "intent":"delegate","reply":"Drafted.","delegation_brief":""})}]}]}
        with self.assertRaises(HTTPException):
            meita_chat_delegate.parse_response(no_brief)
        self.assertEqual(TestClient(app).get("/api/meita/history").status_code,401)

if __name__=="__main__":
    unittest.main()
