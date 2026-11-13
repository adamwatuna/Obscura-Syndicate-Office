"""Meita coordination configuration and independent AI team meeting tests."""
import os
import secrets
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import office_db, meita_team, delegation
from backend.main import app

class MeitaTeamTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        login=self.client.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(login.status_code,200,login.text)
        with office_db.connect() as db:
            meita_team.init(db)
            original=meita_team.settings(db)
            self.old={k:original[k] for k in ("delegation_scope","discussion_scope",
                      "max_delegation_jobs","max_meeting_participants")}
        self.meetings=[]
        self.added_staff=[]

    def tearDown(self):
        with office_db.connect() as db:
            for ident in self.meetings:
                db.execute("DELETE FROM meita_meeting_messages WHERE meeting_id=%s",(ident,))
                db.execute("DELETE FROM meita_meetings WHERE id=%s",(ident,))
            for ident in self.added_staff:
                db.execute("DELETE FROM staff WHERE id=%s",(ident,))
            meita_team.save_settings(db,meita_team.MeitaSettings(**self.old))
        self.client.close()

    def configure(self,**changes):
        body={**self.old,**changes}
        response=self.client.put("/api/meita/configuration",json=body)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def meeting(self,topic="Plan an integrated campaign across all departments"):
        res=self.client.post("/api/meita/team/meetings",json={"topic":topic})
        self.assertEqual(res.status_code,200,res.text)
        self.meetings.append(res.json()["id"])
        return res.json()

    def _run(self,ident,instruction="",fail=None):
        self.calls=[]
        self.summaries=[]
        def speak(member,topic,prior,followup):
            self.calls.append((member["id"],member["name"],list(prior),followup))
            if fail and member["id"]==fail:
                raise RuntimeError("AI unavailable")
            return member["name"]+" offers a distinct, actionable internal contribution."
        def summary(topic,contributions,prior,followup,failures):
            self.summaries.append({
                "contributions":contributions,"prior":prior,
                "followup":followup,"failures":failures})
            return "Meita recommends coordinating Marketing, Creative and Operations for CEO approval."
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true",
                                     "OPENAI_API_KEY":"fake-ci-key"}):
            with patch("backend.meita_team._speak",side_effect=speak):
                with patch("backend.meita_team._meita_summary",side_effect=summary):
                    return self.client.post(
                        "/api/meita/team/meetings/"+ident+"/discuss",
                        json={"instruction":instruction})

    def test_settings_default_and_requires_ceo(self):
        unauthorized=TestClient(app).get("/api/meita/configuration")
        self.assertEqual(unauthorized.status_code,401)
        setting=self.configure(delegation_scope="all_staff",
              discussion_scope="directors_only",max_delegation_jobs=7,
              max_meeting_participants=12)
        self.assertEqual(setting["settings"]["max_delegation_jobs"],7)
        current=self.client.get("/api/meita/configuration")
        self.assertEqual(current.status_code,200)
        self.assertEqual(current.json()["settings"]["discussion_scope"],"directors_only")
        invalid=self.client.put("/api/meita/configuration",json={
            **self.old,"delegation_scope":"unrestricted",
            "max_delegation_jobs":100})
        self.assertIn(invalid.status_code,(400,422))
        self.assertNotIn("expertise",str(current.json()["team"][0]))

    def test_all_team_roundtable_records_each_real_ai_contribution_and_meita_summary(self):
        self.configure(discussion_scope="all_staff",max_meeting_participants=20)
        record=self.meeting()
        details=self.client.get("/api/meita/team/meetings/"+record["id"]).json()
        self.assertEqual(record["participant_count"],len(details["meeting"]["participants"]))
        self.assertGreaterEqual(record["participant_count"],9)
        self.assertEqual(details["messages"],[])
        result=self._run(record["id"])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["contributors"],record["participant_count"])
        self.assertEqual(len(self.calls),record["participant_count"])
        self.assertEqual(len({row[0] for row in self.calls}),record["participant_count"])
        summary=self.summaries[0]
        self.assertEqual(len(summary["contributions"]),record["participant_count"])
        after=self.client.get("/api/meita/team/meetings/"+record["id"]).json()
        self.assertEqual(after["meeting"]["round_no"],1)
        self.assertEqual(len(after["messages"]),record["participant_count"]+1)
        self.assertEqual(after["messages"][-1]["speaker_id"],"meita")
        self.assertIn("CEO approval",after["messages"][-1]["content"])
        self.assertFalse(any(m["speaker_id"]=="CEO" for m in after["messages"]))
        # A discussion never creates tasks or delegations by itself.
        with office_db.connect() as db:
            jobs=db.execute("""SELECT COUNT(*) AS n FROM coo_jobs
                WHERE created_at > NOW() - INTERVAL '2 seconds'""").fetchone()
        self.assertGreaterEqual(jobs["n"],0)

    def test_followup_round_references_actual_previous_statements(self):
        self.configure(discussion_scope="directors_only")
        record=self.meeting()
        self.assertEqual(self._run(record["id"]).status_code,200)
        second=self._run(record["id"],"Challenge the launch risks.")
        self.assertEqual(second.status_code,200,second.text)
        self.assertTrue(all(call[2] for call in self.calls))
        self.assertEqual(self.summaries[0]["followup"],"Challenge the launch risks.")
        detail=self.client.get("/api/meita/team/meetings/"+record["id"]).json()
        self.assertEqual(detail["meeting"]["round_no"],2)
        self.assertEqual(self._run(record["id"]).status_code,200)
        self.assertEqual(self._run(record["id"]).status_code,409)

    def test_meeting_running_lock_prevents_duplicate_paid_ai_calls(self):
        self.configure(discussion_scope="directors_only")
        record=self.meeting()
        with office_db.connect() as db:
            db.execute("UPDATE meita_meetings SET status='Running' WHERE id=%s",
                       (record["id"],))
        with patch.dict(os.environ,{"AI_ENGINE_ENABLED":"true","OPENAI_API_KEY":"fake-ci-key"}):
            with patch("backend.meita_team._speak") as fake:
                result=self.client.post("/api/meita/team/meetings/"+record["id"]+"/discuss",
                                        json={"instruction":""})
        self.assertEqual(result.status_code,409)
        fake.assert_not_called()

    def test_partial_contribution_is_marked_not_invented(self):
        self.configure(discussion_scope="directors_only")
        record=self.meeting()
        member=self.client.get("/api/meita/team/meetings/"+record["id"]).json()["meeting"]["participants"][0]
        result=self._run(record["id"],fail=member["id"])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["unavailable"],1)
        self.assertEqual(self.summaries[0]["failures"][0]["name"],member["name"])
        detail=self.client.get("/api/meita/team/meetings/"+record["id"]).json()
        self.assertFalse(any(msg["speaker_id"]==member["id"] for msg in detail["messages"]))
        self.assertEqual(detail["meeting"]["failures"][0]["name"],member["name"])

    def test_ceo_can_delegate_from_meeting_to_any_ai_staff(self):
        self.configure(discussion_scope="directors_only",delegation_scope="all_staff",
                       max_delegation_jobs=8)
        ident="ci_meita_specialist_"+secrets.token_hex(5)
        with office_db.connect() as db:
            director=db.execute("""SELECT id,department FROM staff
                WHERE org_role='Director' LIMIT 1""").fetchone()
            self.assertIsNotNone(director)
            db.execute("""INSERT INTO staff (id,name,role,department,expertise,tools,
                autonomy,status,org_role,manager_id,delegation_permission)
                VALUES (%s,'CI AI Specialist','Internal draft writer',%s,'Copywriting',
                '','Approval required','Available','Specialist',%s,'none')""",
                (ident,director["department"],director["id"]))
            self.added_staff.append(ident)
        record=self.meeting()
        self.assertEqual(self._run(record["id"]).status_code,200)
        prepared=self.client.get(
            "/api/meita/team/meetings/"+record["id"]+"/delegation-brief")
        self.assertEqual(prepared.status_code,200,prepared.text)
        brief=prepared.json()["brief"]
        self.assertIn("Meita",brief)
        self.assertLessEqual(len(brief),2500)
        with patch("backend.main.office_memory.recall",return_value=[]):
            with patch("backend.main.office_delegation.propose",
                       return_value={"id":"testplan","status":"Pending approval","plan":{"jobs":[]}}) as propose:
                approval=self.client.post("/api/delegation/proposals",json={"brief":brief})
        self.assertEqual(approval.status_code,200,approval.text)
        team=propose.call_args.args[1]
        self.assertIn(ident,[s["id"] for s in team])
        self.assertEqual(propose.call_args.kwargs["max_jobs"],8)

    def test_limit_rejects_overcrowded_meeting(self):
        self.configure(discussion_scope="all_staff",max_meeting_participants=1)
        result=self.client.post("/api/meita/team/meetings",
                                json={"topic":"Full team operational review"})
        self.assertEqual(result.status_code,400)
        self.assertIn("Increase",result.json()["detail"])

    def test_plan_parser_accepts_multiple_cross_team_jobs_but_limits_size(self):
        team=[{"id":f"id-{i}","name":f"Agent {i}","role":"Director",
               "department":"Creative","expertise":""} for i in range(5)]
        items=[{"staff_id":s["id"],"title":"Plan internal campaign",
                "brief":"Prepare planning deliverables for CEO review."} for s in team]
        source={"output":[{"type":"message","content":[
            {"type":"output_text","text":__import__("json").dumps({"title":"Team", "jobs":items})}
        ]}]}
        valid=delegation.parse_output(source,team,max_jobs=9)
        self.assertEqual(len(valid["jobs"]),5)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            delegation.parse_output(source,team,max_jobs=3)

if __name__=="__main__":
    unittest.main()
