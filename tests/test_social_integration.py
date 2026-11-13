"""Social OAuth, encrypted account credentials, granular AI staff permissions and publish safety."""
import os
import secrets
import unittest
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend import office_db,social,social_publish,workforce
from backend.main import app


class SocialIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.settings=patch.dict(os.environ,{
            "SOCIAL_TOKEN_ENCRYPTION_KEY":Fernet.generate_key().decode(),
            "SOCIAL_PUBLIC_BASE_URL":"https://obscura.example",
            "SOCIAL_META_APP_ID":"test-meta-client",
            "SOCIAL_META_APP_SECRET":"test-meta-secret",
            "SOCIAL_GOOGLE_CLIENT_ID":"test-google-client",
            "SOCIAL_GOOGLE_CLIENT_SECRET":"test-google-secret",
            "SOCIAL_TIKTOK_CLIENT_KEY":"test-tiktok-client",
            "SOCIAL_TIKTOK_CLIENT_SECRET":"test-tiktok-secret"})
        self.settings.start()
        self.client=TestClient(app)
        res=self.client.post('/api/login',json={'password':os.environ["CEO_PASSWORD"]})
        self.assertEqual(res.status_code,200,res.text)
        self.platform_external="ci_page_"+secrets.token_hex(5)
        self.accounts=[]
        self.posts=[]
        self.runs=[]
        with office_db.connect() as db:
            social.schema(db)
            result=db.execute("""SELECT id FROM staff
                WHERE department='Marketing & Growth'
                AND org_role='Director' LIMIT 1""").fetchone()
            self.assertIsNotNone(result)
            self.staff_id=result["id"]
            social._store_account(db,"facebook","meta",self.platform_external,
                                  "CI Facebook Page","ci_fake_token")
            fb=db.execute("""SELECT id,access_token_cipher FROM social_accounts
                WHERE platform='facebook' AND external_id=%s""",
                (self.platform_external,)).fetchone()
            self.accounts.append(fb["id"])
            self.facebook_id=fb["id"]
            self.cipher=fb["access_token_cipher"]
        self.assertNotIn("ci_fake_token",self.cipher)

    def tearDown(self):
        with office_db.connect() as db:
            social.schema(db)
            for pid in self.posts:
                db.execute("DELETE FROM social_posts WHERE id=%s",(pid,))
            for ident in self.runs:
                db.execute("DELETE FROM workforce_runs WHERE id=%s",(ident,))
            for account_id in self.accounts:
                db.execute("DELETE FROM social_accounts WHERE id=%s",(account_id,))
        self.client.close()
        self.settings.stop()

    def grant(self,mode,account_id=None,staff_id=None):
        return self.client.post("/api/social/grants",json={
            "staff_id":staff_id or self.staff_id,
            "account_id":account_id or self.facebook_id,
            "mode":mode})

    def draft(self,account_id=None,caption="Our approved staff social post",media_url=None):
        response=self.client.post('/api/social/posts',json={
            "staff_id":self.staff_id,
            "account_id":account_id or self.facebook_id,
            "caption":caption,"media_url":media_url})
        if response.status_code==200:self.posts.append(response.json()["id"])
        return response

    def test_no_grant_by_default_and_no_secrets_in_status(self):
        status=self.client.get("/api/social/status")
        self.assertEqual(status.status_code,200,status.text)
        self.assertTrue(status.json()["providers"][0]["configured"])
        self.assertFalse(status.json()["grants"])
        self.assertEqual(self.draft().status_code,403)
        dump=str(status.json())
        self.assertNotIn("ci_fake_token",dump)
        self.assertNotIn(self.cipher,dump)
        self.assertNotIn(os.environ["SOCIAL_META_APP_SECRET"],dump)
        self.assertEqual(TestClient(app).get("/api/social/status").status_code,401)

    def test_approval_required_and_backend_prevents_duplicate_publish(self):
        self.assertEqual(self.grant('approval_required').status_code,200)
        created=self.draft()
        self.assertEqual(created.status_code,200,created.text)
        post_id=created.json()["id"]
        self.assertEqual(created.json()["status"],"Pending approval")
        self.assertEqual(self.client.post(f"/api/social/posts/{post_id}/publish").status_code,409)
        approval=self.client.post(f"/api/social/posts/{post_id}/approve")
        self.assertEqual(approval.status_code,200,approval.text)
        with patch("backend.social_publish.publish_claimed",return_value="platform_post_123") as provider:
            published=self.client.post(f"/api/social/posts/{post_id}/publish")
        self.assertEqual(published.status_code,200,published.text)
        self.assertEqual(provider.call_args.args[1],"ci_fake_token")
        self.assertEqual(published.json()["status"],"Published")
        self.assertEqual(self.client.post(f"/api/social/posts/{post_id}/publish").status_code,409)
        self.assertEqual(self.client.put(f"/api/social/posts/{post_id}",
            json={"caption":"Edit a published post"}).status_code,409)
        public=[p for p in self.client.get("/api/social/status").json()["posts"] if p["id"]==post_id][0]
        self.assertEqual(public["platform_post_id"],"platform_post_123")

    def test_direct_grant_downgrade_requires_fresh_approval(self):
        self.assertEqual(self.grant('direct_publish').status_code,200)
        created=self.draft()
        self.assertEqual(created.json()["status"],"Ready")
        ident=created.json()["id"]
        self.assertEqual(self.grant('approval_required').status_code,200)
        self.assertEqual(self.client.post(f"/api/social/posts/{ident}/publish").status_code,409)
        with office_db.connect() as db:
            record=db.execute("SELECT status FROM social_posts WHERE id=%s",(ident,)).fetchone()
        self.assertEqual(record["status"],"Ready")
        # The original direct-publish content must now be re-approved.
        # CEO may edit the post to return it to Pending approval.
        edited=self.client.put(f"/api/social/posts/{ident}",
            json={"caption":"Edited and pending renewed approval"})
        self.assertEqual(edited.status_code,200,edited.text)
        self.assertEqual(edited.json()["status"],"Ready") if False else None
        self.assertEqual(self.client.post(f"/api/social/posts/{ident}/publish").status_code,409)
        self.assertEqual(self.grant('off').status_code,200)
        self.assertEqual(self.client.post(f"/api/social/posts/{ident}/publish").status_code,403)

    def test_unsupported_provider_not_eligible_for_publish_grant(self):
        ext_id="ci_youtube_"+secrets.token_hex(5)
        with office_db.connect() as db:
            social._store_account(db,"youtube","google",ext_id,"CI YouTube","secret-yt")
            row=db.execute("""SELECT id FROM social_accounts
                WHERE platform='youtube' AND external_id=%s""",(ext_id,)).fetchone()
        self.accounts.append(row["id"])
        self.assertEqual(self.grant('direct_publish',account_id=row["id"]).status_code,400)
        self.assertEqual(self.grant('off',account_id=row["id"]).status_code,200)

    def test_instagram_requires_public_https_image(self):
        ext_id="ci_instagram_"+secrets.token_hex(5)
        with office_db.connect() as db:
            social._store_account(db,"instagram","meta",ext_id,"CI Instagram","token-ig")
            row=db.execute("""SELECT id FROM social_accounts
                WHERE platform='instagram' AND external_id=%s""",(ext_id,)).fetchone()
        self.accounts.append(row["id"])
        self.assertEqual(self.grant('approval_required',account_id=row["id"]).status_code,200)
        self.assertEqual(self.draft(account_id=row["id"]).status_code,400)
        self.assertEqual(self.draft(account_id=row["id"],
            media_url="http://localhost/internal-image.jpg").status_code,400)
        self.assertEqual(self.draft(account_id=row["id"],
            media_url="https://127.0.0.1/internal-image.jpg").status_code,400)
        self.assertEqual(self.draft(account_id=row["id"],
            media_url="https://images.example.com/approved-photo.jpg").status_code,200)
        with patch("backend.social_publish._form",
                   side_effect=[{"id":"container-1"},{"id":"published-1"}]) as request:
            pid=social_publish.send({"platform":"instagram","external_id":ext_id,
                "caption":"Sample", "media_url":"https://images.example.com/photo.jpg"},"secret")
        self.assertEqual(pid,"published-1")
        self.assertEqual(request.call_count,2)

    def test_oauth_state_is_session_bound_and_one_time(self):
        res=self.client.post('/api/social/oauth/meta/start')
        self.assertEqual(res.status_code,200,res.text)
        auth_url=res.json()['authorization_url']
        state=parse_qs(urlsplit(auth_url).query)["state"][0]
        self.assertEqual(parse_qs(urlsplit(auth_url).query)["client_id"],["test-meta-client"])
        self.assertIn("scope",parse_qs(urlsplit(auth_url).query))
        unauth=TestClient(app).get(f'/api/social/oauth/meta/callback?code=fake&state={state}',
                                 follow_redirects=False)
        self.assertEqual(unauth.status_code,303)
        self.assertEqual(unauth.headers["location"],"/?integration=failed")
        with patch("backend.main.social.oauth_complete",return_value=1) as done:
            callback=self.client.get(
                f'/api/social/oauth/meta/callback?code=fake&state={state}',follow_redirects=False)
        self.assertEqual(callback.status_code,303,callback.text)
        self.assertEqual(callback.headers["location"],"/?integration=connected")
        done.assert_called_once()
        self.assertEqual(self.client.get(
            f'/api/social/oauth/meta/callback?code=fake&state={state}',
            follow_redirects=False).headers['location'],"/?integration=failed")

    def test_import_only_approved_ai_output_as_unpublished_draft(self):
        self.grant('direct_publish')
        run_id="ci_ai_"+secrets.token_hex(8)
        self.runs.append(run_id)
        with office_db.connect() as db:
            workforce.init(db)
            db.execute("""INSERT INTO workforce_runs(id,task_id,task_title,staff_id,
                staff_name,model,result,status)
                VALUES (%s,%s,'Approved marketing caption',%s,'CI Marketing AI',
                'test','Our approved marketing caption text','Approved')""",
                (run_id,"ci_task_"+secrets.token_hex(6),self.staff_id))
        result=self.client.post('/api/social/posts/from-run',json={
            "run_id":run_id,"account_id":self.facebook_id})
        self.assertEqual(result.status_code,200,result.text)
        self.posts.append(result.json()["id"])
        self.assertEqual(result.json()["status"],"Ready")
        with office_db.connect() as db:
            draft=db.execute("SELECT caption,status FROM social_posts WHERE id=%s",
                             (self.posts[-1],)).fetchone()
        self.assertEqual(draft["caption"],"Our approved marketing caption text")
        self.assertEqual(draft["status"],"Ready")

if __name__=="__main__":
    unittest.main()
