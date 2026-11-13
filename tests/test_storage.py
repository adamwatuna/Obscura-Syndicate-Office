"""Integration checks against ephemeral PostgreSQL in GitHub Actions."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from backend import office_db
from backend.main import Staff, Task, Lesson, create_staff, create_task, create_lesson, office

class OfficePersistenceTests(unittest.TestCase):
    def test_new_staff_tasks_lessons_use_postgres(self):
        marker="CI-"+uuid4().hex[:12]
        staff_id=create_staff(Staff(name=marker,role="Creative Director",
            department="Creative",expertise="Direction"))["id"]
        task_id=create_task(Task(title=marker+" brief",assignee=staff_id))["id"]
        lesson_id=create_lesson(Lesson(title=marker+" SOP",kind="Note",
            source="No external actions",assigned=staff_id))["id"]
        current=office()
        self.assertTrue(any(r["id"]==staff_id for r in current["staff"]))
        self.assertTrue(any(r["id"]==task_id for r in current["tasks"]))
        self.assertTrue(any(r["id"]==lesson_id for r in current["lessons"]))
        with office_db.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) AS n FROM staff WHERE id=%s",
                (staff_id,)).fetchone()["n"],1)

    def test_read_only_idempotent_sqlite_import(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"previous.sqlite3"
            legacy_staff="legacy-"+uuid4().hex[:12]
            with sqlite3.connect(path) as old:
                old.execute("""CREATE TABLE staff (
                    id TEXT PRIMARY KEY,name TEXT,role TEXT,department TEXT,
                    expertise TEXT,tools TEXT,autonomy TEXT,status TEXT)""")
                old.execute("""CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,title TEXT,assignee TEXT,status TEXT,created INTEGER)""")
                old.execute("""CREATE TABLE meita_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,role TEXT,content TEXT,created INTEGER)""")
                old.execute("""CREATE TABLE audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT,detail TEXT,created INTEGER)""")
                old.execute("""INSERT INTO staff VALUES (?,?,?,?,?,?,?,?)""",
                    (legacy_staff,"Director","Creative Director","Creative","Art","",
                     "Approval required","Available"))
                old.execute("INSERT INTO tasks VALUES (?,?,?,?,?)",
                    (legacy_staff+"task","Draft trailer",legacy_staff,"To do",1760000000))
                old.execute("INSERT INTO meita_messages(role,content,created) VALUES (?,?,?)",
                    ("user",legacy_staff+" conversation",1760000000))
                old.execute("INSERT INTO audit(action,detail,created) VALUES (?,?,?)",
                    ("staff.hired",legacy_staff,1760000000))
            first=office_db.bootstrap(legacy_path=path)
            second=office_db.bootstrap(legacy_path=path)
            self.assertEqual(first["source_rows"]["staff"],1)
            self.assertEqual(first["copied_rows"]["staff"],1)
            self.assertEqual(second["copied_rows"]["staff"],0)
            self.assertEqual(second["copied_rows"]["meita_messages"],0)
            self.assertTrue(path.is_file(),"Original SQLite must not be deleted")
            with office_db.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) AS n FROM staff WHERE id=%s",
                    (legacy_staff,)).fetchone()["n"],1)
                self.assertEqual(db.execute("SELECT COUNT(*) AS n FROM meita_messages WHERE content=%s",
                    (legacy_staff+" conversation",)).fetchone()["n"],1)
                self.assertEqual(db.execute("SELECT COUNT(*) AS n FROM audit WHERE detail=%s",
                    (legacy_staff,)).fetchone()["n"],1)
                # Existing PG edits are never overwritten by subsequent imports.
                db.execute("UPDATE staff SET name=%s WHERE id=%s",("Edited in PG",legacy_staff))
            office_db.bootstrap(legacy_path=path)
            with office_db.connect() as db:
                self.assertEqual(db.execute("SELECT name FROM staff WHERE id=%s",
                    (legacy_staff,)).fetchone()["name"],"Edited in PG")

if __name__ == "__main__":
    unittest.main()
