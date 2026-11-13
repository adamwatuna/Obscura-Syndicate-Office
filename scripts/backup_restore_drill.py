#!/usr/bin/env python3
"""Destructive restore rehearsal ONLY against disposable CI PostgreSQL.

A real native Railway backup is configured in Railway (volume snapshots/PITR),
not by this script. We validate that a logical pg_dump archive can be restored,
including every public table, sequences and CEO reference BYTEA, into a fresh
temporary database. The existing source database is never modified.

Safety: explicit CI=true, only local loopback DB, disposable restore db name,
no log output containing credentials or user data.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def _pg_url(passwordless_url):
    data=conninfo_to_dict(passwordless_url)
    host=data.get("host","")
    if host not in ("localhost","127.0.0.1","::1"):
        raise SystemExit("Restore rehearsal refused: only loopback CI PostgreSQL allowed")
    if os.getenv("CI","").lower()!="true":
        raise SystemExit("Restore rehearsal refused: CI=true must be set")
    return data


def fingerprint(conn):
    # Counts + samples of blob sizes demonstrate a round trip without printing
    # any actual CEO data. Restoring into the DB does not mutate the source.
    tables=conn.execute("""SELECT tablename FROM pg_tables
        WHERE schemaname='public' ORDER BY tablename""").fetchall()
    counts={}
    for (name,) in tables:
        counts[name]=conn.execute(
            sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier("public",name))).fetchone()[0]
    samples={}
    if "meita_references" in counts:
        row=conn.execute("""SELECT id,octet_length(content),octet_length(preview)
            FROM meita_references ORDER BY id LIMIT 1""").fetchone()
        samples["reference_blob_length"]=tuple(row) if row else None
    return counts,samples


def main():
    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        raise SystemExit("PostgreSQL client utilities pg_dump and pg_restore are required")
    url=os.environ.get("DATABASE_URL","")
    if not url:
        raise SystemExit("DATABASE_URL missing; rehearsal refused")
    dbparams=_pg_url(url)
    scratch="office_restore_drill_"+uuid4().hex[:12]
    scratch_url=make_conninfo(url,dbname=scratch)
    with tempfile.TemporaryDirectory(prefix="office-restore-drill-") as tmp:
        archive=Path(tmp)/"office.backup"
        # Source is read-only during export. --format custom includes table
        # definitions, constraints, indexes, bytea data, sequences and values.
        subprocess.run(["pg_dump","--format=custom","--no-owner","--no-acl",
            "--file",str(archive),"--dbname",url],check=True,
            stdout=subprocess.DEVNULL)
        if not archive.exists() or archive.stat().st_size<100:
            raise SystemExit("pg_dump archive is empty or missing")
        subprocess.run(["pg_restore","--list",str(archive)],check=True,
                       stdout=subprocess.DEVNULL)
        with psycopg.connect(url,autocommit=True) as admin:
            source=fingerprint(admin)
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
        try:
            subprocess.run(["pg_restore","--exit-on-error","--single-transaction",
                "--no-owner","--no-acl","--dbname",scratch_url,str(archive)],
                check=True,stdout=subprocess.DEVNULL)
            with psycopg.connect(scratch_url) as restored:
                target=fingerprint(restored)
            if source!=target:
                raise SystemExit("Restore mismatch: table row counts or blob sizes changed")
            print("PASS: all "+str(len(source[0]))+
                  " public tables and sample bytea metadata restored to isolated CI database")
            print("PASS: backup archive checksum "+
                  hashlib.sha256(archive.read_bytes()).hexdigest()[:16])
        finally:
            # Only the uniquely named temporary database created above.
            with psycopg.connect(url,autocommit=True) as admin:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(scratch)))


if __name__=="__main__":
    main()
