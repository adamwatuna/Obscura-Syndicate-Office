"""Regression checks for the CEO Executive HQ dashboard layout and actual-data visuals."""
import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from backend.main import app


class ExecutiveHQDashboardTests(unittest.TestCase):
    def test_executive_dashboard_has_visual_kpis_and_quick_actions(self):
        source=(Path(__file__).resolve().parents[1]/"frontend"/"index.html").read_text()
        self.assertIn('id="pageTitle">Executive HQ</h1>',source)
        self.assertIn('hq:\'Executive HQ\'',source)
        for name in ("hqTaskDonut","hqDepartmentBars","hqWeeklyChart","hqWeeklyTotal",
                     "hqBriefReview","hqBriefWorking","hqBriefBlocked","hqBriefItems",
                     "hqHeroAvatars","hqStaff"):
            self.assertEqual(source.count('id="'+name+'"'),1,name)
        for action in ("Talk to Meita","Assign a task","View leadership team"):
            self.assertIn(action,source)
        self.assertIn("renderOfficeCharts(s,t)",source)
        self.assertIn("renderExecutivePulse(t,liveActivity)",source)
        self.assertIn("renderLeadershipSpotlight(s)",source)
        self.assertNotIn("Example revenue",source)

    def test_graphs_use_task_timestamps_and_real_execution_status(self):
        source=(Path(__file__).resolve().parents[1]/"frontend"/"index.html").read_text()
        self.assertIn("h q", "h q") if False else None
        self.assertIn("hqDateMalaysia(task.created)",source)
        self.assertIn("timeZone:'Asia/Kuala_Lumpur'",source)
        self.assertIn("tasks.filter(t=>t.status==='For approval')",source)
        self.assertIn("jobs.filter(j=>j.status==='Running')",source)
        self.assertIn("jobs.filter(j=>['Retry available','Needs verification'].includes(j.status))",source)
        self.assertIn("tasks created this week",source)
        self.assertIn("Not a measure of AI hours worked.",source)

    def test_no_change_to_authenticated_routes_or_staff_hierarchy(self):
        page=TestClient(app).get("/")
        self.assertEqual(page.status_code,200)
        self.assertIn("renderWorkforceHierarchy(s)",page.text)
        self.assertIn("workforce-specialist-grid",page.text)
        self.assertIn("id=\"meitaChatCard\"",page.text)
        self.assertIn("id=\"meitaUploadMenu\"",page.text)
        self.assertEqual(TestClient(app).get("/api/office").status_code,401)
        self.assertEqual(TestClient(app).get("/api/ai/diagnostics").status_code,401)


if __name__=="__main__":
    unittest.main()
