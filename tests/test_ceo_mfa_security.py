"""End-to-end CEO authenticator MFA and session invalidation on ephemeral CI DB."""
import os
import secrets
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend import ceo_security, office_db
from backend.main import app


class CEOMFASecurityTests(unittest.TestCase):
    def setUp(self):
        self._reset()
        self.client=TestClient(app)
        login=self.client.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(login.status_code,200,login.text)

    def tearDown(self):
        self._reset()
        self.client.close()

    def _reset(self):
        # These tests only run on GitHub CI's disposable database.
        self.assertIn("office_ci",os.getenv("DATABASE_URL",""),"Never reset production MFA")
        with office_db.connect() as db:
            ceo_security.init(db)
            db.execute("""UPDATE ceo_security SET totp_secret_encrypted=NULL,
                pending_secret_encrypted=NULL,pending_created=NULL,pending_attempts=0,
                recovery_hashes='[]'::jsonb,session_version=0,last_totp_step=-1,
                action_failures=0,action_locked_until=NULL WHERE id=1""")

    def _enroll(self):
        result=self.client.post("/api/security/mfa/setup",json={
            "password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(result.status_code,200,result.text)
        secret=result.json()["secret"]
        self.assertIn("otpauth://",result.json()["otpauth_url"])
        self.assertEqual(len(secret),32)
        code=ceo_security.current_code(secret)
        verify=self.client.post("/api/security/mfa/verify",json={"code":code})
        self.assertEqual(verify.status_code,200,verify.text)
        self.assertTrue(verify.json()["ok"])
        self.assertEqual(len(verify.json()["recovery_codes"]),10)
        self.assertEqual(self.client.get("/api/security/status").json()["recovery_codes_remaining"],10)
        return secret,verify.json()["recovery_codes"]

    def test_mfa_enable_requires_correct_password_and_totp(self):
        before=self.client.post("/api/security/mfa/setup",json={"password":"wrong"})
        self.assertEqual(before.status_code,403)
        setup=self.client.post("/api/security/mfa/setup",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(setup.status_code,200)
        wrong=self.client.post("/api/security/mfa/verify",json={"code":"123456"})
        self.assertEqual(wrong.status_code,200)
        self.assertFalse(wrong.json()["ok"])
        secret=setup.json()["secret"]
        good=self.client.post("/api/security/mfa/verify",json={
            "code":ceo_security.current_code(secret)})
        self.assertTrue(good.json()["ok"])
        self.assertTrue(self.client.get("/api/security/status").json()["mfa_enabled"])

    def test_mfa_login_recovery_one_use_and_session_revocation(self):
        other=TestClient(app)
        self.assertEqual(other.post("/api/login",json={
            "password":os.environ["CEO_PASSWORD"]}).status_code,200)
        secret,recovery=self._enroll()
        # New MFA invalidates password-only sessions without requiring a restart.
        self.assertEqual(other.get("/api/office").status_code,401)
        other.close()
        visitor=TestClient(app)
        password_only=visitor.post("/api/login",json={"password":os.environ["CEO_PASSWORD"]})
        self.assertEqual(password_only.status_code,401)
        otp=visitor.post("/api/login",json={"password":os.environ["CEO_PASSWORD"],
                                             "otp":recovery[0]})
        self.assertEqual(otp.status_code,200,otp.text)
        self.assertEqual(visitor.get("/api/security/status").json()["recovery_codes_remaining"],9)
        visitor.post("/api/logout")
        reused=visitor.post("/api/login",json={"password":os.environ["CEO_PASSWORD"],
                                                "otp":recovery[0]})
        self.assertEqual(reused.status_code,401)
        fresh=visitor.post("/api/login",json={"password":os.environ["CEO_PASSWORD"],
                                              "otp":recovery[1]})
        self.assertEqual(fresh.status_code,200,fresh.text)
        visitor.close()

    def test_enable_disable_revokes_other_sessions_and_clears_secret(self):
        secret,codes=self._enroll()
        visitor=TestClient(app)
        self.assertEqual(visitor.post("/api/login",json={
            "password":os.environ["CEO_PASSWORD"],"otp":codes[0]}).status_code,200)
        disabled=self.client.post("/api/security/mfa/disable",json={
            "password":os.environ["CEO_PASSWORD"],"code":codes[1]})
        self.assertEqual(disabled.status_code,200,disabled.text)
        self.assertTrue(disabled.json()["ok"])
        self.assertFalse(self.client.get("/api/security/status").json()["mfa_enabled"])
        self.assertEqual(visitor.get("/api/office").status_code,401)
        visitor.close()
        with office_db.connect() as db:
            row=db.execute("SELECT totp_secret_encrypted,recovery_hashes FROM ceo_security WHERE id=1").fetchone()
            self.assertIsNone(row["totp_secret_encrypted"])
            self.assertEqual(row["recovery_hashes"],[])

    def test_unauthorized_mfa_endpoints_and_recovery_not_exposed(self):
        anon=TestClient(app)
        self.assertEqual(anon.get("/api/security/status").status_code,401)
        self.assertEqual(anon.post("/api/security/mfa/setup",json={
            "password":os.environ["CEO_PASSWORD"]}).status_code,401)
        secret,codes=self._enroll()
        logged_in=self.client.get("/api/security/status").text
        self.assertNotIn(secret,logged_in)
        self.assertNotIn(codes[0],logged_in)
        db_state=self.client.get("/api/office").text
        self.assertNotIn(secret,db_state)
        anon.close()

    def test_login_options_and_security_ui(self):
        self.assertFalse(self.client.get("/api/security/login-options").json()["mfa_required"])
        self._enroll()
        self.assertTrue(self.client.get("/api/security/login-options").json()["mfa_required"])
        page=Path(__file__).resolve().parents[1]/"frontend"/"index.html"
        source=page.read_text()
        for item in ("ceoMfaStatus","ceoMfaVerifyCode","ceoRecoveryText",
                     "loginMfaCode","security/mfa/setup","security/mfa/disable"):
            self.assertIn(item,source)
        self.assertNotIn("Multi-factor authentication, multi-user roles, account recovery",source)


if __name__=="__main__":
    unittest.main()
