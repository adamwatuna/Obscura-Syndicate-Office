# MEITA Platform integration

The optional Platform route uses the saved MEITA agent for Meita Chat. It saves
the CEO message and confirmed reply to the existing chat history. Explicit
internal assignment requests can produce a pending proposal using current
eligible staff. CEO approval creates real assignments via Obscura's existing
transactional approval flow. Run AI remains a separate action. Directors and
Specialists continue to use the existing Obscura workflows.

| Surface | Context given to MEITA |
| --- | --- |
| Meita Chat | Current message, eight recent messages and selected reference summaries and visual previews |
| Task Centre | Forty recent active task IDs, titles, assignees and statuses |
| Live Desk | Twenty recent job IDs, task IDs, titles, statuses and sources |
| Learning Academy | Relevant text excerpts using the existing retrieval rules |
| Submitted Work | Recent delivery IDs, task IDs, titles and statuses |

The samples are bounded. Exact replays of an assignment request with the same
reference IDs reuse its pending or approved proposal. The application validates
the plan and creates assignments only in the existing CEO approval screen.
Original attachment binaries and submitted report bodies stay in Obscura.

## Activation after approval

Use an isolated Railway environment for verification. The current Obscura
production service tracks GitHub main, so merging can trigger deployment.
Set the following on the server side only.

- `MEITA_PLATFORM_ENABLED` defaults to `false`.
- `MEITA_PLATFORM_AGENT_ID` identifies the saved agent in the selected project.
- `MEITA_PLATFORM_API_KEY` is a restricted credential from that same project.
  Grant Agents read/write and Responses write as required for session inference.
  Supply the value through Railway's secret configuration.
- `AI_ENGINE_ENABLED` must be true for inference, consistent with the existing
  AI configuration. The existing `OPENAI_API_KEY` stays on the legacy provider.

Private chat uses no execution environment, hosted tools, local function tools
or subagents. The saved agent's web search is for standalone public research.
Each chat request makes a fresh session with bounded local history. OpenAI's
session retention applies to the data the app submits. Confirm project data
handling before enabling on private records.

Run the offline suite first. With approval for a paid test, use one synthetic
staging chat message and compare staff, tasks, queue and delivery counts before
and after. Verify approval creates one task per proposed job, repeated requests
reuse one proposal, and pending proposals do not add jobs. Verify chat history
records a reply only after a completed turn.
Treat a timeout as an uncertain upstream outcome before retrying. To roll back
the route, set `MEITA_PLATFORM_ENABLED` to false in the approved environment.

## Director and Specialist agents

Set `STAFF_PLATFORM_ENABLED=true` and `STAFF_PLATFORM_API_KEY` on the
server to opt the staff workforce into OpenAI Agents sessions. This applies to
Director delegation planning and the shared workforce execution path. Set
`STAFF_PLATFORM_AGENT_IDS` to an optional JSON object mapping Obscura staff
IDs to unique saved Agent IDs in that project. Profiles without a saved ID
run an inline agent in their own session. Obscura supplies the assigned
profile's role, scope, configured model and task input on every run.

Only source-backed research and news runs get live web search. Research
deliverables require provider citation annotations. The app keeps task
ownership, output persistence, and approvals in the existing workflows.
Verify the connection with synthetic planning and workforce briefs before
enabling it for real records. Set `STAFF_PLATFORM_ENABLED=false` to return
staff runs to the existing Responses provider.
