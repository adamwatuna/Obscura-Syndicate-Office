# Meita Acting COO — Direct Chat to Autonomous Internal Delivery

## CEO workflow

1. Open Meita → Chat.
2. Give a direct instruction: "Meita, assign Faris to research competitor channels, Aina to draft a marketing plan and Maryam to develop launch concepts. Run the AI work and deliver an executive briefing."
3. Meita uses structured AI intent parsing and internal staff planning. It verifies staff IDs and CEO configuration again before assignment.
4. When Automatic internal execution is ON (default), the CEO's instruction itself authorizes internal work. Meita records its approved proposal, jobs and real Task Centre rows in PostgreSQL — no extra approval click.
5. A database-backed worker processes queued AI jobs. A restart before a job starts will not lose queued work. Staff results appear in Task Centre, Meita Task History and Meita Presents.
6. Meita Presents provides real 16:9 slides and an editable PowerPoint (.pptx) from saved staff work. Charts reflect actual task execution, never invented company metrics.
7. Clearly labelled, traceable and unverified AI report excerpts appear in Memory Centre.

When auto execution is OFF in Meita Configuration, chat creates a pending delegation proposal with Approve & Assign controls. Existing manual approval flows also create Task Centre assignments.

## Meita page layout

- Chat
- Task History
- Meita Presents / Deliverables
- Configuration

The older War Room and Delegation Desk workflows are not displayed in Meita; their backend endpoints remain for compatibility.

## Safety

Meita autonomous execution is restricted to internal AI writing, research drafts, brainstorming and planning. It does not automatically post to social platforms, send emails or messages, pay invoices, deploy code, change security settings or access bank accounts. These actions need separate CEO permissions. Verify sourced claims before external use.

CEO authentication is required. Each instruction may create at most 1–9 internal AI jobs, according to the CEO's configuration. Chat routing and planning consume OpenAI API credits in addition to the employee execution calls.

## Reliability

- AI_ENGINE_ENABLED=true and OPENAI_API_KEY must be configured in Railway.
- MEITA_AUTO_WORKER_ENABLED is true by default; set to false to pause autonomous execution without deleting queued tasks.
- PostgreSQL row locks prevent two application instances from claiming the same queued job.
- Queued jobs resume after a restart. In-flight interrupted jobs are reconciled against saved results; if uncertain, they are marked Needs verification instead of automatically repeated.
- Confirmed failures are marked Retry available. The CEO can explicitly trigger a retry in Task History.
- Memory delivery notes do not overwrite CEO-edited memory records.
- Decks are generated on demand and do not send files to an external image service.

## Scope

The PowerPoint cover includes original abstract editable shapes. Charts describe actual task counts. Images from outside sources are not invented or fetched. Full reports remain available beneath the summarized slides.
