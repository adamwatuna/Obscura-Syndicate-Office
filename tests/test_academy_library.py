"""End-to-end Learning Academy access, shared references and CEO vision tests."""
import os
import secrets
import time
import unittest

from fastapi.testclient import TestClient
from backend import academy, office_db, references
from backend.main import app


class SharedAcademyTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        login=self.client.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(login.status_code,200,login.text)
        self.reference_ids=[]
        self.lesson_ids=[]
        self.message_ids=[]

    def tearDown(self):
        with office_db.connect() as db:
            for ident in self.lesson_ids:
                db.execute("DELETE FROM lessons WHERE id=%s",(ident,))
            for ident in self.message_ids:
                db.execute("DELETE FROM meita_messages WHERE id=%s",(ident,))
            for ident in self.reference_ids:
                db.execute("DELETE FROM meita_references WHERE id=%s",(ident,))
        self.client.close()

    def upload(self,name="bollywood-moodboard.txt",data=b"Pink purple Bollywood stage"):
        r=self.client.post("/api/meita/references/upload",
            files={"file":(name,data,"text/plain")})
        self.assertEqual(r.status_code,200,r.text)
        ident=r.json()["id"]
        self.reference_ids.append(ident)
        return ident

    def test_meita_uploaded_source_automatically_appears_in_academy(self):
        ident=self.upload()
        r=self.client.get("/api/academy/library?search=bollywood")
        self.assertEqual(r.status_code,200,r.text)
        matches=[x for x in r.json()["items"] if x["id"]==ident]
        self.assertEqual(len(matches),1)
        self.assertEqual(matches[0]["type"],"reference")
        self.assertEqual(matches[0]["scope"],"all")

    def test_edit_guidance_and_selective_staff_retrieval(self):
        ident=self.upload()
        with office_db.connect() as db:
            team=db.execute("SELECT id,name,department FROM staff ORDER BY id LIMIT 1").fetchone()
        self.assertIsNotNone(team)
        scope="staff:"+team["id"]
        r=self.client.patch("/api/academy/references/"+ident,json={
            "title":"Bollywood visual language",
            "notes":"Preserve purple pleated costume and pink stage",
            "tags":"Bollywood, purple, pink",
            "scope":scope,"archived":False})
        self.assertEqual(r.status_code,200,r.text)
        with office_db.connect() as db:
            allowed=academy.relevant(db,"Bollywood pink stage costume",
                staff=dict(team))
            denied=academy.relevant(db,"Bollywood pink stage costume",
                staff={"id":"unknown","name":"Other","department":"Unknown"})
        self.assertIn(ident,allowed["reference_ids"])
        self.assertIn("Preserve purple pleated costume",allowed["context"])
        self.assertNotIn(ident,denied["reference_ids"])
        self.assertNotIn("Preserve purple pleated costume",denied["context"])

    def test_invalid_department_scope_is_rejected(self):
        ident=self.upload()
        r=self.client.patch("/api/academy/references/"+ident,json={
            "title":"","notes":"","tags":"","scope":"department:NotARealDepartment",
            "archived":False})
        self.assertEqual(r.status_code,400)

    def test_archived_reference_is_not_injected_to_staff(self):
        ident=self.upload()
        r=self.client.patch("/api/academy/references/"+ident,json={
            "title":"Bollywood design", "notes":"","tags":"","scope":"all",
            "archived":True})
        self.assertEqual(r.status_code,200)
        with office_db.connect() as db:
            self.assertNotIn(ident,academy.relevant(db,"Bollywood design")["reference_ids"])
        self.assertNotIn(ident,[x["id"] for x in self.client.get("/api/academy/library").json()["items"]])
        self.assertIn(ident,[x["id"] for x in self.client.get(
            "/api/academy/library?include_archived=true").json()["items"]])

    def test_ceo_chat_vision_can_be_promoted_once(self):
        with office_db.connect() as db:
            row=db.execute("""INSERT INTO meita_messages(role,content,created)
                VALUES('user',%s,%s) RETURNING id""",
                ("The film Hasad must have cinematic dark suspense with smooth title reveals.",
                 int(time.time()))).fetchone()
            self.message_ids.append(row["id"])
        url="/api/academy/visions/message/"+str(row["id"])
        first=self.client.post(url)
        self.assertEqual(first.status_code,200,first.text)
        self.assertTrue(first.json()["created"])
        self.lesson_ids.append(first.json()["id"])
        second=self.client.post(url)
        self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(second.json()["id"],first.json()["id"])
        self.assertFalse(second.json()["created"])
        result=self.client.get("/api/academy/library?search=Hasad").json()
        self.assertTrue(any(x["id"]==first.json()["id"] and x["kind"]=="CEO Vision"
                            for x in result["items"]))
        with office_db.connect() as db:
            found=academy.relevant(db,"Hasad cinematic dark suspense title")
        self.assertIn("Hasad",found["context"])

    def test_cannot_promote_ai_assistant_message_as_ceo_vision(self):
        with office_db.connect() as db:
            row=db.execute("""INSERT INTO meita_messages(role,content,created)
                VALUES('assistant',%s,%s) RETURNING id""",
                ("An unapproved AI idea",int(time.time()))).fetchone()
            self.message_ids.append(row["id"])
        r=self.client.post("/api/academy/visions/message/"+str(row["id"]))
        self.assertEqual(r.status_code,404)

    def test_old_lesson_registration_remains_compatible(self):
        text="Manual creative note "+secrets.token_hex(4)
        r=self.client.post("/api/lessons",json={
            "title":"New creative learning note","kind":"Note",
            "source":text,"assigned":""})
        self.assertEqual(r.status_code,200,r.text)
        self.lesson_ids.append(r.json()["id"])
        found=self.client.get("/api/academy/library?search="+text.replace(" ","%20"))
        self.assertEqual(found.status_code,200,found.text)
        self.assertTrue(any(x["id"]==r.json()["id"] for x in found.json()["items"]))

    def test_unauthenticated_library_rejected(self):
        anon=TestClient(app)
        self.assertEqual(anon.get("/api/academy/library").status_code,401)
        self.assertEqual(anon.post("/api/academy/visions/message/1").status_code,401)
        anon.close()


if __name__=="__main__":
    unittest.main()
