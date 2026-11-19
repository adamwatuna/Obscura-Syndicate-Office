# MEITA Platform integration

The optional Platform route uses the saved MEITA agent for Meita Chat. It saves
the CEO message and confirmed reply to the existing chat history. It has no
path into the task creation, staffing, assignment, submission or approval code.
Directors and Specialists continue to use the existing Obscura workflows.

| Surface | Context given to MEITA |
| --- | --- |
| Meita Chat | Current message, eight recent messages and selected reference summaries and visual previews |
| Task Centre | Forty recent active task IDs, titles, assignees and statuses |
| Live Desk | Twenty recent job IDs, task IDs, titles, statuses and sources |
| Learning Academy | Relevant text excerpts using the existing retrieval rules |
| Submitted Work | Recent delivery IDs, task IDs, titles and statuses |

The samples are bounded. The agent can discuss existing records and suggest work
for review. Task execution and approval remain in their existing app screens.
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
and after. Verify chat history records a reply only after a completed turn.
Treat a timeout as an uncertain upstream outcome before retrying. To roll back
the route, set `MEITA_PLATFORM_ENABLED` to false in the approved environment.
