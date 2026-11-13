"""Full 45 specialist staffing, CEO-independent audit, finance mode and UI header regression."""
import os
import re
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from backend import office_db, specialist_seed, department_playbooks, organization
from backend.main import app


class FullWorkforceImplementationTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        response=self.client.post('/api/login',json={'password':os.environ['CEO_PASSWORD']})
        self.assertEqual(response.status_code,200,response.text)
        self.draft_ids=[]

    def tearDown(self):
        with office_db.connect() as db:
            for ident in self.draft_ids:
                db.execute("DELETE FROM finance_budget_drafts WHERE id=%s",(ident,))
        self.client.close()

    def test_all_nine_directors_45_specialists_and_direct_reporting(self):
        with office_db.connect() as db:
            staff=db.execute("""SELECT id,name,role,department,org_role,manager_id
                FROM staff WHERE org_role IN ('Director','Specialist','Independent Auditor')""").fetchall()
            directors={r['department']:r for r in staff if r['org_role']=='Director'}
            for department,config in department_playbooks.DEPARTMENTS.items():
                self.assertIn(department,directors)
                specialists=[r for r in staff if
                    r['department']==department and r['org_role']=='Specialist']
                titles={r['role'] for r in specialists}
                for title,_ in config['roles']:
                    self.assertIn(title,titles,department)
                    person=next(p for p in specialists if p['role']==title)
                    self.assertEqual(person['manager_id'],directors[department]['id'])
            self.assertEqual(len(directors),9)
            auditor=[p for p in staff if p['org_role']=='Independent Auditor']
            self.assertEqual(len(auditor),1)
            self.assertIsNone(auditor[0]['manager_id'])
            self.assertEqual(auditor[0]['department'],'Independent Audit')
            self.assertEqual(sum(len(x['roles']) for x in
                department_playbooks.DEPARTMENTS.values()),45)

    def test_seed_once_and_no_restart_rehire(self):
        with office_db.connect() as db:
            count=db.execute("SELECT COUNT(*) AS n FROM staff").fetchone()['n']
            result=specialist_seed.install(db)
            self.assertFalse(result['applied'])
            self.assertEqual(count,db.execute("SELECT COUNT(*) AS n FROM staff").fetchone()['n'])

    def test_independent_auditor_excluded_from_meita_delegation(self):
        from backend import meita_team
        with office_db.connect() as db:
            selected=meita_team.available_staff(db,"all_staff")
        self.assertFalse(any(p['org_role']=='Independent Auditor' for p in selected))
        with office_db.connect() as db:
            allowed=meita_team.available_staff(db,"directors_only")
        self.assertEqual(len([p for p in allowed if p['org_role']=='Director']),9)

    def test_protect_independent_auditor_from_director_or_meita_line(self):
        from backend.main import Staff
        with office_db.connect() as db:
            try:
                organization.validate_staff(db, Staff(
                    name='Imposter',role='AI Internal Audit Specialist',
                    department='Finance & Treasury',org_role='Independent Auditor'))
            except Exception as error:
                self.assertEqual(getattr(error,'status_code',None),400)
            else: self.fail('Independent Auditor cannot report under Finance')

    def test_finance_budget_scenario_is_only_projections(self):
        r=self.client.get('/api/finance/planning')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['mode'],'planning_only')
        self.assertFalse(r.json()['bank_connected'])
        payload={
            'title':'Film production 2027 plan','period':'2027-01','scenario':'Base case',
            'currency':'MYR','projected_revenue':'12000',
            'costs':[{'category':'Production','amount':'4000'},
                     {'category':'Marketing','amount':'2500'}],
            'assumptions':'Hypothetical estimates; no bank data'
        }
        out=self.client.post('/api/finance/planning/drafts',json=payload)
        self.assertEqual(out.status_code,200,out.text)
        self.draft_ids.append(out.json()['id'])
        self.assertEqual(out.json()['projected_costs'],'6500.00')
        self.assertEqual(out.json()['projected_surplus'],'5500.00')
        self.assertIn('PROJECTIONS ONLY',out.json()['data_status'])
        rows=self.client.get('/api/finance/planning').json()['drafts']
        self.assertTrue(any(x['id']==out.json()['id'] for x in rows))

    def test_finance_rejects_invalid_numeric_inputs(self):
        base={
            'title':'Test budget scenario','period':'2027-01','scenario':'Base case',
            'currency':'MYR','projected_revenue':'100',
            'costs':[{'category':'Cost','amount':'25'}]}
        for val in ('NaN','Infinity','-5'):
            proposal=dict(base,projected_revenue=val)
            self.assertEqual(self.client.post('/api/finance/planning/drafts',
                json=proposal).status_code,422)

    def test_unauthorized_finance_endpoint(self):
        anon=TestClient(app)
        self.assertEqual(anon.get('/api/finance/planning').status_code,401)
        anon.close()

    def test_no_duplicate_primary_page_headers(self):
        source=(Path(__file__).resolve().parents[1]/'frontend'/'index.html').read_text()
        # Each view has one global dynamic h1, not a second page-title h2.
        self.assertEqual(source.count('id="pageTitle"'),1)
        views=re.split(r'<section class="view(?: active)?" id="',source)
        for view in views[1:]:
            part=view.split('<section class="view')[0].split('</main>')[0]
            self.assertNotRegex(part,r'<h2\b[^>]*>[^<]*(?:Learning Academy|AI Workforce|Task Centre|Live Desks|Monthly Director Workplan|Research Intelligence|Security & Governance)[^<]*</h2>')

    def test_finance_staff_policy_blocks_claiming_banking_actuals(self):
        policy=department_playbooks.job_policy('Finance & Treasury','AI Financial Analyst')
        self.assertIn('planning-only',policy)
        self.assertIn('No bank account connection',policy)
        self.assertIn('payments',policy)
        self.assertIn('confidence',department_playbooks.job_policy(
            'Research & Intelligence','AI Fact-Checking Specialist'))


if __name__=="__main__":
    unittest.main()
