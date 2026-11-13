"""Regression: no PostgreSQL schema migrations on concurrent live API reads."""
import os
import concurrent.futures
import unittest
from fastapi.testclient import TestClient

from backend import academy, references, delegation, workforce, meita_autowork
from backend import organization, meita_team, monthly, task_schedule
from backend import finance_planning, ceo_security, deliverables, memory
from backend import office_db, schema_state
from backend.main import app


class BlockAnyDDL:
    def execute(self,*args,**kwargs):
        raise AssertionError("DDL attempted after office startup")


class SchemaRequestSafetyTests(unittest.TestCase):
    def test_schema_setup_is_not_repeated_from_live_requests(self):
        self.assertTrue(schema_state._ready,"Bootstrap should have completed at startup")
        fake=BlockAnyDDL()
        for mod,fn in [
            (academy,"init"),(references,"init"),(delegation,"init"),
            (workforce,"init"),(meita_autowork,"init"),(organization,"init_plans"),
            (meita_team,"init"),(monthly,"setup"),(task_schedule,"setup"),
            (finance_planning,"init"),(ceo_security,"init"),
            (deliverables,"init"),(memory,"setup"),(office_db,"ensure_login_security")
        ]:
            with self.subTest(mod=mod.__name__):
                getattr(mod,fn)(fake)

    def test_explicit_bootstrap_allows_migrations(self):
        class Probe:
            seen=False
            def execute(self,query,*args,**kwargs):
                self.seen=True
        probe=Probe()
        with schema_state.migration():
            office_db.ensure_login_security(probe)
        self.assertTrue(probe.seen)

    def test_parallel_authenticated_academy_and_meita_reads_no_deadlock(self):
        client=TestClient(app)
        try:
            logged=client.post("/api/login",json={
                "password":os.environ["CEO_PASSWORD"]})
            self.assertEqual(logged.status_code,200,logged.text)
            urls=["/api/academy/library?search=&kind=all&include_archived=false",
                  "/api/meita/history","/api/meita/task-history",
                  "/api/office","/api/ai/diagnostics"]
            def fetch(index):
                u=urls[index%len(urls)]
                res=client.get(u)
                return u,res.status_code,res.text[:130]
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results=list(executor.map(fetch,range(24)))
            for path,status,body in results:
                self.assertEqual(status,200,(path,body))
        finally:
            client.close()


if __name__=="__main__":
    unittest.main()
