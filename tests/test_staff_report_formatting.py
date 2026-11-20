"""Contract tests for ChatGPT-style staff report rendering in the CEO office UI."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

HTML = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


class StaffReportFormattingTests(unittest.TestCase):
    def test_reports_are_rich_and_research_uses_same_renderer(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("function staffReportRender(value)", html)
        self.assertIn("full.append(staffReportRender(report.result))", html)
        self.assertIn("report.replaceChildren(staffReportRender(full.result))", html)
        for marker in ("report-rich h1", "report-rich h2", "report-rich pre",
                       "report-rich table", "report-rich blockquote",
                       "report-table-scroll"):
            self.assertIn(marker, html)
        for tag in ("h", "strong", "em", "ul", "ol", "li", "table", "pre", "blockquote"):
            self.assertIn(tag, html)

    def test_ai_report_rendering_is_dom_only_no_html_injection(self):
        html = HTML.read_text(encoding="utf-8")
        start = html.index("function staffReportInline(value,parent)")
        stop = html.index("function deliveryText(tag,value,cls)", start)
        renderer = html[start:stop]
        self.assertNotIn("innerHTML", renderer)
        self.assertNotIn("insertAdjacentHTML", renderer)
        self.assertIn("textContent=", renderer)
        self.assertIn("document.createTextNode", renderer)
        self.assertIn("['https:','http:']", renderer)
        self.assertIn("noopener noreferrer", renderer)
        self.assertNotIn("new Function", renderer)
        self.assertNotIn("eval(", renderer)

    def test_inline_javascript_parses_in_node(self):
        html = HTML.read_text(encoding="utf-8")
        js = html.split("<script>")[-1].split("</script>")[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "office.js"
            path.write_text(js, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)],
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
