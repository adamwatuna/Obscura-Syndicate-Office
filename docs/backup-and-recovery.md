
# Personal AI Private Office — production backup & recovery

**Safety:** PostgreSQL persistent volume is not a backup. The CI backup_restore_drill.py validates a logical pg_dump and pg_restore in a separate disposable localhost PostgreSQL database. Passing CI does not prove that Railway production backups are scheduled.

## Configure native Railway backups (required)

Project: carefree-imagination / production
Database: Postgres / volume: postgres-volume
Railway: https://railway.com/project/a4efe6b2-5b92-4146-99aa-2992c92c5874/service/0cc38e76-a9ce-47bb-ba1c-7db12d9a2071?environmentId=716d6622-3ee0-4c68-bd2d-0f5165d9975e

1. Open Postgres -> Backups in Railway. Enable automatic daily + weekly backups. Retain at least seven daily and four weekly snapshots if the plan allows.
2. Create a named pre-launch backup manually and verify it completed, is timestamped, and references the correct Postgres volume.
3. Optionally enable PostgreSQL PITR (point-in-time recovery) for finer restore points; check Railway plan and storage charges first.
4. Verify a fresh backup on Railway weekly. Restore a snapshot into an isolated database/volume; never restore in-place to production merely to test a backup.
5. Record restore duration, row counts, and whether CEO messages, uploaded reference bytes and deliverables are accessible.

CLI (locally signed into Railway; verify supported options with --help):
- railway postgres pitr status --service Postgres
- railway postgres pitr schedule set --service Postgres --daily --weekly
- railway postgres pitr schedule list --service Postgres
- railway postgres pitr backup create --service Postgres --name pre-launch-snapshot

Railway reference: https://docs.railway.com/cli/postgres#manage-backup-schedules

## Automated isolated restore rehearsal

GitHub CI calls CI=true python scripts/backup_restore_drill.py *after* the feature tests. The script refuses non-localhost databases, performs a custom-format pg_dump, verifies the archive, creates a uniquely named disposable local DB, pg_restores in a transaction, compares all table counts plus binary-reference metadata, then removes ONLY its scratch database. Credentials and record contents do not appear in logs.

The CI test does not create or replace any production snapshot.

## Emergency recovery procedure

1. Pause Meita auto-execution and other writers to prevent new jobs while investigating.
2. Identify the latest completed and verified snapshot. Confirm its time and expected potential data loss.
3. Restore to a **separate new database**, not the live one. Verify schema, row counts, references, submissions and output files.
4. Obtain CEO approval before changing DATABASE_URL or performing in-place restoration. Repoint during a controlled maintenance window.
5. Verify health checks, CEO MFA, task history, Learning Academy and deliverables. Reconcile jobs marked Needs verification rather than blindly replaying API calls.
6. Record restoration timestamp, recovered data and any irrecoverable changes.

Backups contain sensitive CEO records. Keep them private, restrict Railway access, and never commit dumps, passwords or recovery codes to GitHub.
