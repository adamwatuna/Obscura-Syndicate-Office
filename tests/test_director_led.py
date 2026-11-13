"""Director-led reporting lines and delegation authorization integration tests."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from backend import office_db
from backend.main import app

class DirectorLedTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        self.assertEqual(self.client.post("/api/login",
            json={"password":os.environ["CEO_PASSWORD"]}).status_code,200)
        self.created=[]
        self.restores=[]
        self.plans=[]
    def tearDown(self):
        with office_db.connect() as db:
            for ident in self.plans:
                db.execute('DELETE FROM director_jobs WHERE plan_id=%s',(ident,))
                db.execute('DELETE FROM director_plans WHERE id=%s',(ident,))
            for ident,autonomy,permission in self.restores:
                db.execute('UPDATE staff SET autonomy=%s,delegation_permission=%s WHERE id=%s',(autonomy,permission,ident))
            for ident in self.created:
                db.execute("DELETE FROM director_jobs WHERE director_id=%s",(ident,))
                db.execute("DELETE FROM director_plans WHERE director_id=%s",(ident,))
            for ident in self.created:
                db.execute("UPDATE staff SET manager_id=NULL WHERE manager_id=%s",(ident,))
                db.execute("DELETE FROM staff WHERE id=%s",(ident,))
        self.client.close()

    def hire(self,name,role,dept,org_role="Specialist",manager_id=None,
             delegation_permission="approval_required",autonomy="Approval required"):
        data={"name":name,"role":role,"department":dept,"org_role":org_role,
              "manager_id":manager_id,"delegation_permission":delegation_permission,
              "expertise":"Internal drafts only","autonomy":autonomy,"tools":""}
        res=self.client.post("/api/staff",json=data)
        self.assertEqual(res.status_code,200,res.text)
        ident=res.json()["id"]
        self.created.append(ident)
        return ident,data

    def existing_director(self,dept,routine=False):
        with office_db.connect() as db:
            row=db.execute("""SELECT id,autonomy,delegation_permission FROM staff
                WHERE department=%s AND org_role='Director'""",(dept,)).fetchone()
            self.assertIsNotNone(row,dept+' should be seeded')
            if routine:
                self.restores.append((row['id'],row['autonomy'],row['delegation_permission']))
                db.execute("""UPDATE staff SET autonomy='Routine internal tasks only',
                    delegation_permission='department_internal' WHERE id=%s""",(row['id'],))
            return row['id']

    def test_scoped_department_and_routine_delegation(self):
        key=uuid4().hex[:8]
        director=self.existing_director("Marketing & Growth",routine=True)
        specialist,_=self.hire("Campaigner-"+key,"Campaign Specialist","Marketing & Growth",
            manager_id=director)
        outsider,_=self.hire("Researcher-"+key,"Research Specialist","Research & Intelligence")
        public=self.client.get("/api/office").json()["staff"]
        self.assertEqual(next(r for r in public if r["id"]==specialist)["manager_id"],director)
        self.assertNotIn("expertise",next(r for r in public if r["id"]==director))
        self.assertEqual(self.client.get("/api/organization").status_code,200)
        self.assertEqual(TestClient(app).get("/api/organization").status_code,401)

        attempt=self.client.post("/api/staff",json={
            "name":"Invalid-"+key,"role":"Specialist","department":"Research & Intelligence",
            "org_role":"Specialist","manager_id":director})
        self.assertEqual(attempt.status_code,400)
        duplicate=self.client.post("/api/staff",json={
            "name":"Duplicate-"+key,"role":"CMO","department":"Marketing & Growth",
            "org_role":"Director"})
        self.assertEqual(duplicate.status_code,409)

        plan={"title":"Marketing launch","jobs":[
            {"staff_id":specialist,"staff_name":"Campaigner-"+key,
             "title":"Draft campaign captions","brief":"Write five original internal draft captions."}]}
        with patch("backend.organization._openai_plan",return_value=plan):
            proposed=self.client.post(f"/api/directors/{director}/plans",
                json={"brief":"Prepare internal campaign materials for CEO review"})
        self.assertEqual(proposed.status_code,200,proposed.text)
        self.assertEqual(proposed.json()["status"],"Approved")
        plan_id=proposed.json()["id"]
        self.plans.append(plan_id)
        job=next(j for j in self.client.get("/api/directors/plans").json()["jobs"]
                 if j["plan_id"]==plan_id)
        with patch("backend.organization.workforce.execute",
                   return_value={"id":"mock-run-id","status":"For approval",
                                 "result":"Draft created"}) as execution:
            produced=self.client.post("/api/directors/jobs/"+job["id"]+"/run")
        self.assertEqual(produced.status_code,200,produced.text)
        execution.assert_called_once()
        self.assertEqual(execution.call_args.args[1]["id"],specialist)

        # Even an LLM returning an out-of-department ID cannot bypass scope.
        hostile={"title":"Invalid","jobs":[{"staff_id":outsider,"staff_name":"Researcher",
            "title":"Cross department work","brief":"Collect data from another department."}]}
        with patch("backend.organization._openai_plan",return_value=hostile):
            refused=self.client.post(f"/api/directors/{director}/plans",
                json={"brief":"Delegate research outside marketing department"})
        self.assertEqual(refused.status_code,409)

    def test_finance_requires_ceo_approval_even_with_routine_flag(self):
        key=uuid4().hex[:8]
        director=self.existing_director("Finance & Treasury",routine=True)
        specialist,_=self.hire("Analyst-"+key,"Finance Analyst","Finance & Treasury",
            manager_id=director)
        plan={"title":"Financial review","jobs":[
            {"staff_id":specialist,"staff_name":"Analyst-"+key,
             "title":"Prepare budget notes","brief":"Draft an internal financial review memo."}]}
        with patch("backend.organization._openai_plan",return_value=plan):
            res=self.client.post(f"/api/directors/{director}/plans",
                json={"brief":"Analyze spending assumptions for internal review"})
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(res.json()["status"],"Pending approval")
        self.plans.append(res.json()["id"])
        approved=self.client.post("/api/directors/plans/"+res.json()["id"]+"/approve")
        self.assertEqual(approved.status_code,200,approved.text)
        jobs=self.client.get("/api/directors/plans").json()["jobs"]
        self.assertTrue(any(j["plan_id"]==res.json()["id"] for j in jobs))

if __name__=="__main__":
    unittest.main()
