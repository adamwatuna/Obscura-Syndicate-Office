"""Verify real PostgreSQL director hiring and embedded fictional headshots."""
import base64
import re
import unittest
from pathlib import Path

from backend import leadership_seed, office_db

EXPECTED={
    "Creative":"Maryam",
    "Research & Intelligence":"Faris Iskandar",
    "Marketing & Growth":"Nur Aina Sofea",
    "Finance & Treasury":"Ahmad Fikri",
    "Operations & Project Management":"Nur Syafiqah Iman",
    "Technology & AI Systems":"Daniel Hakim",
    "Strategy & Business Development":"Puteri Balqis",
    "Knowledge & Learning Academy":"Siti Hajar Yasmin",
    "Governance, Risk & Compliance":"Rizqina Nadia",
}

class ExecutiveRosterTests(unittest.TestCase):
    def test_all_nine_executives_are_hired_in_postgres(self):
        with office_db.connect() as db:
            rows=db.execute("""SELECT id,name,department,org_role,portrait_slot,expertise
                FROM staff WHERE org_role='Director'""").fetchall()
        by_dept={row['department']:row for row in rows}
        for department,name in EXPECTED.items():
            self.assertIn(department,by_dept)
            executive=by_dept[department]
            self.assertEqual(executive['name'],name)
            self.assertTrue(executive['expertise'].strip())
            self.assertIsNotNone(executive['portrait_slot'])
        self.assertEqual(sorted(int(r['portrait_slot']) for r in rows
            if r['department'] in EXPECTED),list(range(9)))

    def test_seed_will_not_rehire_director_after_approved_deletion(self):
        with office_db.connect() as db:
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM office_schema_migrations WHERE name=%s",
                (leadership_seed.MIGRATION_KEY,)).fetchone())
            db.execute("SAVEPOINT check_director_deletion")
            try:
                staff=db.execute("""SELECT id FROM staff
                    WHERE department='Research & Intelligence'
                    AND org_role='Director' LIMIT 1""").fetchone()
                self.assertIsNotNone(staff)
                db.execute("DELETE FROM staff WHERE id=%s",(staff['id'],))
                outcome=leadership_seed.install(db)
                self.assertFalse(outcome['applied'])
                self.assertIsNone(db.execute(
                    "SELECT id FROM staff WHERE id=%s",(staff['id'],)).fetchone())
            finally:
                db.execute("ROLLBACK TO SAVEPOINT check_director_deletion")

    def test_embedded_avatars_are_actual_webp_bytes(self):
        html=(Path(__file__).resolve().parent.parent/'frontend'/'index.html').read_text()
        match=re.search(r'const EXECUTIVE_ATLAS = "data:image/webp;base64,([A-Za-z0-9+/=]+)";',html)
        self.assertIsNotNone(match,"Portrait atlas must be present in Workforce UI")
        image=base64.b64decode(match.group(1),validate=True)
        self.assertEqual(image[:4],b'RIFF')
        self.assertEqual(image[8:12],b'WEBP')
        self.assertEqual(int.from_bytes(image[4:8],'little')+8,len(image))
        self.assertGreater(len(image),2500)
        self.assertIn('staffPortrait(s)',html)
        self.assertIn('portrait_slot',html)

if __name__=="__main__":
    unittest.main()
