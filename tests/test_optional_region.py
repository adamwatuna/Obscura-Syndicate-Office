"""Regional scope must be optional in Director monthly tasks."""
import secrets
import time
import unittest
from pathlib import Path
from backend import monthly, office_db

class OptionalRegionalScopeTests(unittest.TestCase):
    def test_default_templates_have_no_forced_region(self):
        for key,department,title,brief,due in monthly.DEFAULTS:
            self.assertNotIn("{region}",title)
            self.assertNotIn("{region}",brief)
            self.assertTrue(brief)
        self.assertEqual(len(monthly.DEFAULTS),18)

    def test_generated_monthly_tasks_have_no_rotating_region(self):
        with office_db.connect() as db:
            db.execute("SAVEPOINT optional_region_generate")
            try:
                report=monthly.generate_month(db,"2097-07")
                self.assertGreaterEqual(report["created"],9)
                self.assertNotIn("region_focus",report)
                entries=monthly.summary(db,"2097-07")
                self.assertNotIn("region_focus",entries)
                for row in entries["assignments"]:
                    self.assertNotIn("{region}",row["title"])
                    self.assertNotIn("{region}",row["brief"])
                    self.assertNotIn("Klang Valley (Kuala Lumpur",row["title"])
            finally:
                db.execute("ROLLBACK TO SAVEPOINT optional_region_generate")

    def test_cleanup_replaces_only_untouched_future_automated_tasks(self):
        old=next(r for r in monthly.LEGACY_DEFAULTS if r[0]=="marketing-calendar")
        new=next(r for r in monthly.DEFAULTS if r[0]=="marketing-calendar")
        old_other=next(r for r in monthly.LEGACY_DEFAULTS if r[0]=="finance-controls")
        future_a="2096-03"
        future_b="2096-04"
        with office_db.connect() as db:
            db.execute("SAVEPOINT optional_region_cleanup")
            try:
                db.execute("DELETE FROM office_schema_migrations WHERE name=%s",
                           ("optional_regional_scope_v1",))
                db.execute("""UPDATE monthly_templates SET title=%s,brief=%s
                    WHERE key=%s""",(old[2],old[3],old[0]))
                # An independently modified template must not be overwritten.
                db.execute("""UPDATE monthly_templates SET title='CEO customized finance title'
                    WHERE key=%s""",(old_other[0],))
                director=db.execute("""SELECT id FROM staff
                    WHERE department=%s AND org_role='Director'""",(old[1],)).fetchone()
                self.assertIsNotNone(director)
                ids=[]
                for period,custom in ((future_a,False),(future_b,True)):
                    focus=monthly._legacy_region_for(period)
                    title=f"{period} · {old[2].replace('{region}',focus)}"
                    prior=old[3].replace("{region}",focus)
                    desc="CEO chose Sabah explicitly for this task" if custom else prior
                    tid=secrets.token_hex(12);ids.append(tid)
                    db.execute("""INSERT INTO tasks
                        (id,title,description,assignee,status,created,priority,due_date,recurrence)
                        VALUES (%s,%s,%s,%s,'To do',%s,'Normal',%s,'Monthly')""",
                        (tid,title,desc,director["id"],int(time.time()),period+"-18"))
                    db.execute("""INSERT INTO monthly_assignments
                        (template_key,period,task_id,director_id,due_date,brief)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                        (old[0],period,tid,director["id"],period+"-18",desc))
                outcome=monthly.remove_automatic_region(db)
                self.assertTrue(outcome["applied"])
                self.assertEqual(outcome["assignments"],1)
                self.assertFalse(monthly.remove_automatic_region(db)["applied"])
                stored=db.execute("""SELECT title,brief FROM monthly_templates
                    WHERE key=%s""",(old[0],)).fetchone()
                self.assertEqual(stored["title"],new[2])
                self.assertEqual(stored["brief"],new[3])
                preserved=db.execute("""SELECT title FROM monthly_templates
                    WHERE key=%s""",(old_other[0],)).fetchone()
                self.assertEqual(preserved["title"],"CEO customized finance title")
                changed=db.execute("SELECT title,description FROM tasks WHERE id=%s",
                                   (ids[0],)).fetchone()
                self.assertEqual(changed["title"],future_a+" · "+new[2])
                self.assertEqual(changed["description"],new[3])
                customized=db.execute("SELECT description FROM tasks WHERE id=%s",
                                      (ids[1],)).fetchone()
                self.assertEqual(customized["description"],
                                 "CEO chose Sabah explicitly for this task")
                updated_brief=db.execute("""SELECT brief FROM monthly_assignments
                    WHERE task_id=%s""",(ids[0],)).fetchone()
                self.assertEqual(updated_brief["brief"],new[3])
            finally:
                db.execute("ROLLBACK TO SAVEPOINT optional_region_cleanup")

    def test_dashboard_has_description_only_guidance(self):
        html=(Path(__file__).resolve().parent.parent/"frontend"/"index.html").read_text()
        self.assertNotIn('id="monthlyRegion"',html)
        self.assertNotIn("monthlyData.region_focus",html)
        self.assertIn("state, city or region to the task description",html)

if __name__=="__main__":
    unittest.main()
