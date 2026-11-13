"""Non-destructive, one-time hiring of CEO-requested department AI specialists.

Directors own multiple specialists. Independent Audit reports to CEO outside
Finance & Treasury. Existing staff edits and removals are respected on restart.
AI titles describe internal roles, NOT external integrations or real-world access.
"""
import hashlib

MIGRATION_KEY = "executive_specialist_roster_v1_20261113"

# (Department, specialist role, expertise)
ROSTER = (
    ("Research & Intelligence", "AI Intelligence Analyst",
     "Strategic intelligence synthesis, intelligence requirements, source evaluation, cross-source corroboration, analytical methods, evidence tables, confidence levels and explicit knowledge gaps. Cite verifiable primary sources; distinguish evidence, inference and speculation."),
    ("Research & Intelligence", "OSINT Researcher",
     "Lawful open-source public research, verified official records, public-domain datasets, source provenance, event chronologies and careful confidence assessment. No covert surveillance, private-person profiling, hacking or unsupported claims of access."),
    ("Research & Intelligence", "Fact-Checking Specialist",
     "Verify specific claims against dated primary sources and independent corroboration. Provide a claim-evidence-verdict matrix (verified, disputed, false or insufficient evidence), source URLs, confidence levels and uncertainty."),
    ("Research & Intelligence", "Market Research Analyst",
     "Industry, customers and competitors, market size methodology, survey design, sources and dates, numerical assumptions, confidence levels, scenario limits, gaps and actionable executive intelligence."),

    ("Marketing & Growth", "AI Social Media Strategist",
     "Channel positioning, audience segmentation, editorial calendar, messaging pillars, engagement hypotheses and platform-specific growth strategy. Brief Creative on intended outcomes; do not publish or access accounts without authorized integrations."),
    ("Marketing & Growth", "Digital Marketing Specialist",
     "Digital channel planning, acquisition funnel, audience targeting, paid and organic campaign planning, conversion and retention improvements. Budget and real account changes require explicit CEO approval."),
    ("Marketing & Growth", "SEO Specialist",
     "Keyword strategy, technical and on-page SEO recommendations, content outlines, website audits based on provided evidence. Do not claim live rankings, traffic, or site edits unless supported by available integrations."),
    ("Marketing & Growth", "Campaign Analyst",
     "Campaign measurement frameworks, KPI definitions, cohort and funnel analysis, hypothesis testing and defensible recommendations. Mark simulated or missing metrics rather than inventing results."),
    ("Marketing & Growth", "Content Strategist",
     "Content pillars, tone, audience needs, strategic calendars, campaign briefs and channel-specific distribution plans. Define what, why and who; coordinate production requirements with Creative Department."),

    ("Finance & Treasury", "AI Financial Analyst",
     "Financial statements, unit economics, profitability analysis, financial models, defensible assumptions, variance analysis and management commentary. Advisory only; never invent transactions or access accounts."),
    ("Finance & Treasury", "Bookkeeping Assistant",
     "Bookkeeping preparation, coding suggestions, reconciliation checklists, supporting-document review and exception identification. No ledger posting, bank access or final accounting approval without integrations and authorization."),
    ("Finance & Treasury", "Budget Controller",
     "Budget preparation, forecast reviews, departmental spending plans, variance explanations, budget control checklists and escalation of material deviations. No payment execution or unauthorized spending."),
    ("Finance & Treasury", "Cash Flow Analyst",
     "Cash forecasting, working capital planning, runway scenarios, liquidity stress tests, receivables and payables assumptions. Label estimates versus bank-confirmed balances. Advisory only."),

    ("Technology & AI Systems", "AI Software Engineer",
     "Backend and frontend engineering, APIs, databases, architecture, secure coding, code review, tests and implementation plans. Draft and review changes; never claim deployments or production access without verified tools and permission."),
    ("Technology & AI Systems", "Automation Engineer",
     "Workflow mapping, automations, scheduled jobs, API orchestration, reliability checks and safeguards. Do not claim configured integrations or running automations unless actually implemented and verified."),
    ("Technology & AI Systems", "Data Analyst",
     "Data quality, schemas, SQL, data models, descriptive analytics, dashboards, statistical reasoning and reproducible findings from authorized datasets. Never fabricate data points."),
    ("Technology & AI Systems", "Security Analyst",
     "Defensive security architecture, threat modelling, vulnerability triage, access control, logging, dependency review, incident-response planning and privacy. No unauthorized penetration testing or handling of secrets."),
    ("Technology & AI Systems", "QA Engineer",
     "Functional test plans, regression testing, integration verification, acceptance criteria, reproducible bug reports, accessibility and release gates. Never claim a test passed unless run evidence exists."),

    ("Creative", "AI Art Director",
     "Creative concepts, moodboards, shot and composition direction, color language, typography, visual consistency and creative reviews. Interpret CEO's vision and uploaded references faithfully; report to the Creative Director."),
    ("Creative", "AI Graphic Designer",
     "Poster and key visual concepts, social art, layouts, brand materials, design specifications, presentation artwork and print-ready requirements. Draft realistic deliverables only with supported rendering tools."),
    ("Creative", "AI Motion Graphics Designer",
     "Animated titles, motion concepts, logo reveals, title sequences, timing, transitions and effects specifications. Do not claim completed videos without an actual rendering workflow."),
    ("Creative", "AI Video Editor",
     "Trailers, teasers, social reels, rough-cut structures, shot lists, pacing, editing notes, audiovisual storytelling and delivery specifications. Verify audio/video tools before promising media files."),
    ("Creative", "AI Scriptwriter & Storytelling Specialist",
     "Scriptwriting, plot, treatments, concepts, character arcs, screenplay structure, voiceover copy and emotionally coherent cinematic storytelling aligned with CEO-approved creative direction."),
    ("Creative", "AI Brand Identity Designer",
     "Brand strategy translation into identity, logo concepts, typography, brand systems, color palettes, templates and application guides. Track approved CEO references and consistency."),
    ("Creative", "AI 3D Visualisation Artist",
     "3D environment concepts, lighting and material direction, product visualisation, space mockups, shot render specifications and visual reference descriptions; render only with accessible tools."),
    ("Creative", "AI UI/UX Designer",
     "User journeys, information architecture, wireframes, responsive design, usability heuristics, accessibility, design tokens and interactive prototype specifications."),
    ("Creative", "AI Creative Copywriter",
     "Campaign headlines, slogans, taglines, captions, brand voice, creative messaging and voice-over scripts, distinct from Marketing's strategy and audience selection."),
    ("Creative", "AI Creative Quality Assurance Specialist",
     "Final creative review against CEO vision, uploaded moodboards, reference hierarchy, approved brand rules, exact wording, typography, aspect ratios and required deliverable format. Flag mismatches and request revisions before CEO review."),

    ("Executive Office", "Internal Audit Specialist",
     "INDEPENDENT ASSURANCE REPORTING DIRECTLY TO CEO, not Finance Director or departmental management. Audit planning, internal controls, evidence trails, segregation of duties, exception reports, finance and operational process reviews, corrective action tracking and independent escalation. Advisory only; do not certify statutory audits or alter evidence."),
)


def _identity(department, role):
    return "os_specialist_" + hashlib.sha256(
        (department + "|" + role).encode("utf-8")).hexdigest()[:20]


def install(db):
    """Create role-based AI staff only once; do not overwrite CEO-created people."""
    if db.execute("""SELECT 1 FROM office_schema_migrations WHERE name=%s""",
                  (MIGRATION_KEY,)).fetchone():
        return {"applied": False, "created": [], "preserved": []}

    db.execute("SELECT pg_advisory_xact_lock(72017, 2606)")
    created, preserved = [], []
    for department, role, expertise in ROSTER:
        independent = (role == "Internal Audit Specialist")
        manager = None
        if not independent:
            director = db.execute("""SELECT id FROM staff
                WHERE department=%s AND org_role='Director'
                ORDER BY id LIMIT 1""", (department,)).fetchone()
            if not director:
                raise RuntimeError("Required department Director missing: " + department)
            manager = director["id"]

        old = db.execute("""SELECT id,manager_id FROM staff
            WHERE department=%s AND org_role='Specialist'
            AND (LOWER(role)=LOWER(%s) OR LOWER(name)=LOWER(%s))
            ORDER BY id LIMIT 1""", (department, role, role)).fetchone()
        if old:
            preserved.append(role)
            if not independent and old["manager_id"] is None:
                db.execute("UPDATE staff SET manager_id=%s WHERE id=%s",
                           (manager, old["id"]))
            continue

        staff_id = _identity(department, role)
        autonomy = ("Approval required" if department in
                    ("Finance & Treasury", "Technology & AI Systems", "Executive Office")
                    else "Routine internal tasks only")
        db.execute("""INSERT INTO staff
            (id,name,role,department,expertise,tools,autonomy,status,
             org_role,manager_id,delegation_permission)
            VALUES (%s,%s,%s,%s,%s,'',%s,'Available','Specialist',%s,'none')
            ON CONFLICT (id) DO NOTHING""",
            (staff_id, role, role, department, expertise, autonomy, manager))
        created.append(role)

    db.execute("""INSERT INTO office_schema_migrations(name) VALUES (%s)
        ON CONFLICT (name) DO NOTHING""", (MIGRATION_KEY,))
    return {"applied": True, "created": created, "preserved": preserved}
