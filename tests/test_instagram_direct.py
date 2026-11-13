"""Instagram Direct Login (no Facebook Page) and Instagram publishing checks."""
import os
import secrets
import unittest
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend import office_db, social, social_publish
from backend.main import app

class InstagramDirectLoginTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{
            "SOCIAL_TOKEN_ENCRYPTION_KEY":Fernet.generate_key().decode(),
            "SOCIAL_PUBLIC_BASE_URL":"https://obscura.example",
            "SOCIAL_INSTAGRAM_APP_ID":"instagram-test-id",
            "SOCIAL_INSTAGRAM_APP_SECRET":"instagram-test-secret",
        })
        self.env.start()
        self.client=TestClient(app)
        r=self.client.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(r.status_code,200,r.text)
        self.ig_id="ci_instagram_direct_"+secrets.token_hex(6)
        self.accounts=[]
        self.posts=[]
        with office_db.connect() as db:
            social.schema(db)
            staff=db.execute("""SELECT id FROM staff WHERE department='Marketing & Growth'
                AND org_role='Director' LIMIT 1""").fetchone()
            self.assertIsNotNone(staff)
            self.staff=staff["id"]

    def tearDown(self):
        with office_db.connect() as db:
            for id in self.posts:
                db.execute("DELETE FROM social_posts WHERE id=%s",(id,))
            for id in self.accounts:
                db.execute("DELETE FROM social_accounts WHERE id=%s",(id,))
        self.client.close()
        self.env.stop()

    def connect(self):
        auth=self.client.post("/api/social/oauth/instagram/start")
        self.assertEqual(auth.status_code,200,auth.text)
        url=auth.json()["authorization_url"]
        parsed=urlsplit(url)
        self.assertEqual(parsed.hostname,"www.instagram.com")
        self.assertEqual(parsed.path,"/oauth/authorize")
        params=parse_qs(parsed.query)
        self.assertEqual(params["client_id"],["instagram-test-id"])
        self.assertEqual(params["scope"],[
            "instagram_business_basic,instagram_business_content_publish"])
        self.assertEqual(params["enable_fb_login"],["false"])
        self.assertTrue(params["redirect_uri"][0].endswith(
            "/api/social/oauth/instagram/callback"))
        calls=[]
        def instagram_http(url,form=None,headers=None,timeout=18):
            calls.append((url,form))
            if url=="https://api.instagram.com/oauth/access_token":
                self.assertEqual(form["client_id"],"instagram-test-id")
                self.assertEqual(form["client_secret"],"instagram-test-secret")
                return {"access_token":"short-lived-ci-instagram-token"}
            if url.startswith("https://graph.instagram.com/access_token?"):
                self.assertIn("ig_exchange_token",url)
                return {"access_token":"long-lived-ci-instagram-token","expires_in":5184000}
            if url.startswith(social.INSTAGRAM_GRAPH+"/me?"):
                return {"user_id":self.ig_id,"username":"ceo.instagram.pro"}
            raise AssertionError("Unexpected provider URL: "+url)
        with patch("backend.social._http_json",side_effect=instagram_http):
            callback=self.client.get(
                "/api/social/oauth/instagram/callback?state="+params["state"][0]+
                "&code=instagram-approved-code",follow_redirects=False)
        self.assertEqual(callback.status_code,303,callback.text)
        self.assertEqual(callback.headers["location"],"/?integration=connected")
        with office_db.connect() as db:
            account=db.execute("""SELECT * FROM social_accounts
                WHERE platform='instagram' AND external_id=%s""",(self.ig_id,)).fetchone()
        self.assertIsNotNone(account)
        self.accounts.append(account["id"])
        self.assertEqual(account["provider"],"instagram")
        self.assertEqual(social.decrypt(account["access_token_cipher"]),
                         "long-lived-ci-instagram-token")
        self.assertEqual(account["display_name"],"ceo.instagram.pro")
        self.assertIsNotNone(account["token_expires_at"])
        return account,calls

    def test_direct_login_requires_no_facebook_or_page(self):
        account,calls=self.connect()
        self.assertEqual(len(calls),3)
        with office_db.connect() as db:
            pages=db.execute("""SELECT id FROM social_accounts
                WHERE provider='meta' AND external_id=%s""",(self.ig_id,)).fetchall()
            self.assertFalse(pages)
        status=self.client.get("/api/social/status")
        self.assertEqual(status.status_code,200,status.text)
        providers=status.json()["providers"]
        direct=next(x for x in providers if x["id"]=="instagram")
        self.assertTrue(direct["configured"])
        self.assertTrue(direct["publishing_available"])
        self.assertIn("no Facebook",direct["name"])
        self.assertNotIn("long-lived-ci-instagram-token",str(status.json()))
        self.assertEqual(TestClient(app).get("/api/social/status").status_code,401)

    def test_direct_instagram_publish_has_no_facebook_graph_calls(self):
        account,_=self.connect()
        no_permission=self.client.post("/api/social/posts",json={
            "staff_id":self.staff,"account_id":account["id"],
            "caption":"Official approved image post",
            "media_url":"https://images.example.com/approved.jpg"})
        self.assertEqual(no_permission.status_code,403)
        grant=self.client.post("/api/social/grants",json={
            "staff_id":self.staff,"account_id":account["id"],
            "mode":"approval_required"})
        self.assertEqual(grant.status_code,200,grant.text)
        draft=self.client.post("/api/social/posts",json={
            "staff_id":self.staff,"account_id":account["id"],
            "caption":"Official approved image post",
            "media_url":"https://images.example.com/approved.jpg"})
        self.assertEqual(draft.status_code,200,draft.text)
        self.posts.append(draft.json()["id"])
        self.assertEqual(draft.json()["status"],"Pending approval")
        self.assertEqual(self.client.post(
            "/api/social/posts/"+self.posts[0]+"/publish").status_code,409)
        approval=self.client.post(
            "/api/social/posts/"+self.posts[0]+"/approve")
        self.assertEqual(approval.status_code,200,approval.text)
        calls=[]
        def fake_form(url,body):
            calls.append(url)
            return {"id":"ci-container-123" if url.endswith("/media") else "ci-post-123"}
        with patch("backend.social_publish._form",side_effect=fake_form):
            published=self.client.post(
                "/api/social/posts/"+self.posts[0]+"/publish")
        self.assertEqual(published.status_code,200,published.text)
        self.assertEqual(published.json()["platform_post_id"],"ci-post-123")
        self.assertEqual(len(calls),2)
        self.assertTrue(all(url.startswith(social.INSTAGRAM_GRAPH+"/") for url in calls))
        self.assertFalse(any("graph.facebook.com" in url for url in calls))

    def test_manual_refresh_replaces_encrypted_token(self):
        account,_=self.connect()
        with patch("backend.social._http_json",return_value={
            "access_token":"refreshed-ig-token","expires_in":5184000}) as request:
            r=self.client.post(
                "/api/social/accounts/"+account["id"]+"/refresh-instagram")
        self.assertEqual(r.status_code,200,r.text)
        self.assertIn("refresh_access_token",request.call_args.args[0])
        with office_db.connect() as db:
            updated=db.execute("SELECT access_token_cipher FROM social_accounts WHERE id=%s",
                               (account["id"],)).fetchone()
        self.assertEqual(social.decrypt(updated["access_token_cipher"]),"refreshed-ig-token")
        self.assertNotEqual(account["access_token_cipher"],updated["access_token_cipher"])

    def test_failed_authorization_not_connected(self):
        auth=self.client.post("/api/social/oauth/instagram/start")
        params=parse_qs(urlsplit(auth.json()["authorization_url"]).query)
        failed=self.client.get(
            "/api/social/oauth/instagram/callback?state="+params["state"][0]+
            "&error=access_denied",follow_redirects=False)
        self.assertEqual(failed.status_code,303)
        self.assertEqual(failed.headers["location"],"/?integration=cancelled")

if __name__=="__main__":
    unittest.main()
