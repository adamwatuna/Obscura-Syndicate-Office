"""Attachment composer and bounded upload regression tests for Meita."""
import io
import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from backend import office_db
from backend.main import app


class MeitaAttachmentComposerTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        response=self.client.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(response.status_code,200,response.text)
        self.uploads=[]

    def tearDown(self):
        with office_db.connect() as db:
            for ref_id in self.uploads:
                db.execute("DELETE FROM meita_references WHERE id=%s",(ref_id,))
        self.client.close()

    def _upload(self,name,raw,mime):
        response=self.client.post("/api/meita/references/upload",
            files={"file":(name,raw,mime)})
        if response.status_code==200:
            self.uploads.append(response.json()["id"])
        return response

    def test_upload_small_image_kept_for_meita_and_learning_academy(self):
        output=io.BytesIO()
        Image.new("RGB",(20,20),"purple").save(output,"PNG")
        uploaded=self._upload("moodboard.png",output.getvalue(),"image/png")
        self.assertEqual(uploaded.status_code,200,uploaded.text)
        ref=uploaded.json()
        self.assertEqual(ref["kind"],"image")
        preview=self.client.get("/api/meita/references/"+ref["id"]+"/content?preview=true")
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.headers["content-type"],"image/jpeg")
        original=self.client.get("/api/meita/references/"+ref["id"]+"/content")
        self.assertEqual(original.content,output.getvalue())
        items=self.client.get("/api/academy/library?kind=reference").json()["items"]
        self.assertTrue(any(x["id"]==ref["id"] for x in items))
        with office_db.connect() as db:
            from backend import references
            self.assertEqual(len(references.images(db,[ref["id"]])),1)

    def test_upload_empty_pdf_signature_and_oversize(self):
        empty=self._upload("empty.txt",b"","text/plain")
        self.assertEqual(empty.status_code,400,empty.text)
        pdf=self._upload("bad.pdf",b"not a PDF","application/pdf")
        self.assertEqual(pdf.status_code,400,pdf.text)
        too_big=self._upload("too-large.txt",b"a"*(20*1024*1024+1),"text/plain")
        self.assertEqual(too_big.status_code,413,too_big.text)
        self.assertIn("20 MB",too_big.json()["detail"])

    def test_requires_authenticated_ceo_session(self):
        client=TestClient(app)
        response=client.post("/api/meita/references/upload",
             files={"file":("note.txt",b"internal","text/plain")})
        self.assertEqual(response.status_code,401)
        self.assertEqual(client.get("/api/academy/library?kind=reference").status_code,401)
        client.close()

    def test_attachment_menu_has_real_supported_actions_not_prompt(self):
        content=(Path(__file__).resolve().parent.parent/"frontend"/"index.html").read_text()
        for identifier in ("meitaUploadMenu","meitaMenuFiles","meitaMenuLibrary","meitaMenuLink",
                           "meitaReferenceInput","meitaAttachedReferences","meitaUploadState"):
            self.assertIn('id="'+identifier+'"',content)
        self.assertIn("academy/library?kind=reference",content)
        self.assertIn("dragover",content)
        self.assertIn("clipboardData",content)
        self.assertIn("meitaUploadFiles",content)
        self.assertNotIn("Type FILE to select attachments",content)
        self.assertNotIn("prompt('Type FILE",content)


if __name__=="__main__":
    unittest.main()
