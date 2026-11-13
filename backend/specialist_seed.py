"""Idempotent, non-destructive first-time hiring for 45 internal AI specialists.

Runs at PostgreSQL bootstrap after nine executive Directors exist.
Existing CEO-hired equivalent specialists are preserved, not duplicated or overwritten.
A migration marker prevents re-hiring deliberately deleted personnel on restart.
"""
import re
from backend import department_playbooks as playbooks

MIGRATION_KEY="department_specialist_workforce_45_budget_planning_20261113"


def _equivalent(title):
    return re.sub(r"\s+"," ",re.sub(r"^AI\s+","",str(title or "").strip(),
                                   flags=re.IGNORECASE)).casefold()


def install(db):
    db.execute("SELECT pg_advisory_xact_lock(92513, 4513)")
    if db.execute("SELECT 1 FROM office_schema_migrations WHERE name=%s",
                  (MIGRATION_KEY,)).fetchone():
        return {"applied":False,"created":0,"preserved":0}
    created=0
    preserved=0
    linked=0
    for dept,details in playbooks.DEPARTMENTS.items():
        director=db.execute("""SELECT id FROM staff WHERE department=%s
            AND org_role='Director' LIMIT 1""",(dept,)).fetchone()
        if not director:
            raise RuntimeError("Cannot seed specialists: missing Director for "+dept)
        existing=db.execute("""SELECT id,role,manager_id FROM staff
            WHERE department=%s AND org_role='Specialist'""",(dept,)).fetchall()
        roles={_equivalent(p["role"]):p for p in existing}
        for index,(role,description) in enumerate(details["roles"],1):
            equivalent=roles.get(_equivalent(role))
            if equivalent:
                preserved+=1
                # Respect existing reporting lines; only attach specialists who
                # don't yet have a manager.
                if equivalent["manager_id"] is None:
                    db.execute("""UPDATE staff SET manager_id=%s
                        WHERE id=%s AND manager_id IS NULL""",(director["id"],equivalent["id"]))
                    linked+=1
                continue
            ident="os_specialist_"+str(list(playbooks.DEPARTMENTS).index(dept)+1).zfill(2)+"_"+str(index).zfill(2)
            expertise=(description+"\n\n"+
                "Reporting line: department Director "+details["director"]+" via Meita (Acting COO). "
                "Department workflow: "+" -> ".join(details["route"])+". "
                "Final work must meet: "+details["deliverables"]+" "
                "Use authorized references from Learning Academy where relevant. "
                "No unauthorized external actions, payments, publishing, account access or invented evidence.")
            db.execute("""INSERT INTO staff
                (id,name,role,department,expertise,tools,autonomy,status,
                 org_role,manager_id,delegation_permission,portrait_slot)
                VALUES (%s,%s,%s,%s,%s,'','Approval required','Available',
                        'Specialist',%s,'none',NULL) ON CONFLICT(id) DO NOTHING""",
                (ident,role,role,dept,expertise,director["id"]))
            roles[_equivalent(role)]={"id":ident,"role":role,"manager_id":director["id"]}
            created+=1
    # Independent audit is NOT a tenth Director and has no Meita manager.
    audit=db.execute("""SELECT id FROM staff
        WHERE department='Independent Audit'
        AND org_role='Independent Auditor' LIMIT 1""").fetchone()
    if not audit:
        audit=playbooks.AUDITOR
        db.execute("""INSERT INTO staff
            (id,name,role,department,expertise,tools,autonomy,status,
             org_role,manager_id,delegation_permission)
            VALUES ('os_independent_auditor_v1',%s,%s,%s,%s,'','Advisory only',
                    'Available','Independent Auditor',NULL,'none')
            ON CONFLICT (id) DO NOTHING""",
            (audit["name"],audit["role"],audit["department"],audit["expertise"]))
        created+=1
    else:
        preserved+=1
    db.execute("""INSERT INTO office_schema_migrations(name)
        VALUES(%s) ON CONFLICT DO NOTHING""",(MIGRATION_KEY,))
    return {"applied":True,"created":created,"preserved":preserved,"linked":linked}
