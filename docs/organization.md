# Director-led AI Private Office

## Organizational command

CEO → Meita (Acting COO) → Department Director → directly assigned AI Specialists.

The Chief Risk & Compliance Officer may escalate independent findings directly to the CEO; no automatic notification service is implemented.

## Supported departments

- Executive Office (Meita)
- Research & Intelligence (Director of Intelligence)
- Marketing & Growth (Chief Marketing Officer)
- Creative (Creative Director)
- Finance & Treasury (Finance Director)
- Operations & Project Management (Head of Operations)
- Technology & AI Systems (Chief Technology Officer)
- Strategy & Business Development (Chief Strategy Officer)
- Knowledge & Learning Academy (Director of Knowledge)
- Governance, Risk & Compliance (Chief Risk & Compliance Officer)

The system does not automatically hire these eight new Directors. The existing Creative Director is recognized by the one-time migration when their current role contains "Creative Director" and they belong to Creative.

## Stored permissions and enforcement

- **Staff role:** Director or Specialist. At most one Director per department (PostgreSQL partial unique index).
- **Reports to:** Specialist can report only to a Director in the same department. New specialists are automatically assigned when there is one; unassigned specialists can be edited. Director reports to Meita as organizational policy.
- **Director delegation:** Director plans use only the current direct-report roster. Model-suggested assignments are validated before being saved, rechecked at CEO approval and at task execution.
- **CEO approval required:** No delegation is automatically approved in Finance & Treasury, Technology & AI Systems, Strategy & Business Development, or Governance, Risk & Compliance. Other Directors also require CEO approval unless explicitly configured for routine internal delegation and given "Routine internal tasks only" autonomy.
- **Routine delegation:** For an eligible Director, a CEO-initiated department brief can create pre-approved *internal* task assignments. It does not automatically generate deliverables or spend money: CEO still triggers "Run AI."
- **Meita coordination:** Meita's delegation planner receives department Directors as its possible assignees. Meita plans still require CEO approval. Director Delegation Desk is where Directors plan their own direct-report assignments.
- **Outputs:** Staff produce internal OpenAI-generated draft deliverables, stored in PostgreSQL and reviewed by the CEO. No publishing, external integrations, payments or cloud infrastructure changes are enabled by these permissions.
- **Deletion/reassignment:** Existing tasks and completed results remain. Removing a Director unassigns its reports and cancels unstarted department jobs; removing a Specialist cancels their unstarted department jobs.

## CEO configuration

AI Workforce → Hire AI staff (or Edit):

1. Select a supported department.
2. Select **Department Director** for a department head, or **AI Specialist** for a direct report.
3. For a Specialist, select the department Director under **Reports to**.
4. For a Director, select the delegation mode. Use **CEO approves department assignments** for higher-risk operations.
5. Save the internal expertise/instructions, which remain out of the general directory.

Director Delegation Desk:

1. Choose a Director and enter a CEO department brief.
2. AI produces a bounded plan using that Director's direct reports.
3. Approve a plan when CEO approval is required.
4. Select **Run AI** on the desired internal assignment.
5. Review the generated result in Task Centre.

All endpoints currently require the authenticated CEO session; they do **not** provide independent credentials or accounts to AI staff. Director authority is enforced by server-side scope on these delegation workflows, not by exposing an unrestricted API to agents.
