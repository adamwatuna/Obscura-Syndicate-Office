# Private Office PostgreSQL backup cron

The service executes an independent read-only pg_dump -Fc to private Railway S3 storage, verifies the archive with pg_restore -l, checks the remote upload SHA256 metadata and object size, then exits. Backup data is never sent to a public URL.

Railway cron: 10 18 * * * (daily 02:10 Malaysia time). Sunday Malaysia time is also weekly backup day. Retention: 14 daily and 8 weekly backups. A failure exits nonzero and does not delete old copies.

Service variables must be Railway references (not literal credentials):
- DATABASE_URL = ${{Postgres.DATABASE_URL}}
- S3_ENDPOINT = ${{ObscuraBackups.ENDPOINT}}
- S3_BUCKET = ${{ObscuraBackups.BUCKET}}
- S3_REGION = ${{ObscuraBackups.REGION}}
- S3_ACCESS_KEY_ID = ${{ObscuraBackups.ACCESS_KEY_ID}}
- S3_SECRET_ACCESS_KEY = ${{ObscuraBackups.SECRET_ACCESS_KEY}}

No public domain, no production data restoration. Always test restoring into an isolated database first. Do not treat a successful upload as proof of a full recovery test. Costs: a Railway cron service and private object storage may incur charges. For higher resilience, keep a second encrypted copy in a different account/provider.
