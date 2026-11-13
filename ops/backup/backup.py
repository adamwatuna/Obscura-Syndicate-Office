"""Daily backup: private, verified custom-format PostgreSQL dump to Railway S3."""
import datetime as dt
import hashlib
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

logger=logging.getLogger("private_office.backup")
PREFIX="obscura-private-office/postgres/"
UTC=dt.timezone.utc
MALAYSIA=dt.timezone(dt.timedelta(hours=8))


def required(key):
    value=os.environ.get(key,"").strip()
    if not value:
        raise RuntimeError("Missing backup configuration: "+key)
    return value


def pg_env(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ("postgres","postgresql") or not parsed.hostname or not parsed.username:
        raise RuntimeError("Invalid PostgreSQL connection URL")
    env=dict(os.environ)
    env.update({"PGHOST":parsed.hostname,"PGPORT":str(parsed.port or 5432),
                "PGUSER":unquote(parsed.username),
                "PGPASSWORD":unquote(parsed.password or ""),
                "PGDATABASE":unquote(parsed.path.lstrip("/"))})
    if parse_qs(parsed.query).get("sslmode"):
        env["PGSSLMODE"]=parse_qs(parsed.query)["sslmode"][0]
    return env


def key_for(now,frequency):
    if frequency not in ("daily","weekly"):
        raise ValueError("Unsupported backup frequency")
    return PREFIX+frequency+"/"+now.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ.dump")


def obsolete_keys(s3,bucket,frequency,keep):
    prefix=PREFIX+frequency+"/"
    match=re.compile("^"+re.escape(prefix)+r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.dump$")
    keys=[]
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket,Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents",[])
                    if match.fullmatch(item.get("Key","")))
    return sorted(keys,reverse=True)[keep:]


def storage():
    import boto3
    from botocore.config import Config
    endpoint=required("S3_ENDPOINT")
    if not endpoint.startswith("https://"):
        raise RuntimeError("S3 endpoint must use HTTPS")
    return boto3.client("s3",endpoint_url=endpoint,
        region_name=required("S3_REGION"),
        aws_access_key_id=required("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=required("S3_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4",retries={"max_attempts":4,"mode":"standard"},
                      s3={"addressing_style":"virtual"}))


def backup(now=None):
    now=now or dt.datetime.now(UTC)
    bucket=required("S3_BUCKET")
    db=pg_env(required("DATABASE_URL"))
    with tempfile.TemporaryDirectory(prefix="obscura-backup-") as root:
        file=Path(root)/"office.dump"
        subprocess.run(["pg_dump","-Fc","-Z","6","--no-owner","--no-acl",
                        "-f",str(file)],env=db,check=True,
                       stdout=subprocess.DEVNULL,timeout=600)
        size=file.stat().st_size
        if size<256 or size>2*1024*1024*1024:
            raise RuntimeError("Backup size outside safety limits")
        subprocess.run(["pg_restore","--list",str(file)],
                       check=True,stdout=subprocess.DEVNULL,timeout=45)
        with file.open("rb") as handle:
            checksum=hashlib.file_digest(handle,"sha256").hexdigest()
        s3=storage()
        daily=key_for(now,"daily")
        s3.upload_file(str(file),bucket,daily,ExtraArgs={
            "ContentType":"application/octet-stream",
            "Metadata":{"sha256":checksum,"format":"pgdump-custom"}})
        object=s3.head_object(Bucket=bucket,Key=daily)
        if int(object.get("ContentLength",0))!=size or object.get("Metadata",{}).get("sha256")!=checksum:
            raise RuntimeError("Uploaded daily backup did not pass remote verification")
        logger.info("Daily backup verified: %s (%s bytes)",daily,size)
        if now.astimezone(MALAYSIA).weekday()==6:
            weekly=key_for(now,"weekly")
            s3.copy_object(Bucket=bucket,CopySource={"Bucket":bucket,"Key":daily},
                           Key=weekly,MetadataDirective="COPY")
            object=s3.head_object(Bucket=bucket,Key=weekly)
            if int(object.get("ContentLength",0))!=size:
                raise RuntimeError("Weekly backup did not pass remote verification")
            logger.info("Weekly backup verified: %s",weekly)
        for frequency,keep in (("daily",14),("weekly",8)):
            for key in obsolete_keys(s3,bucket,frequency,keep):
                s3.delete_object(Bucket=bucket,Key=key)
        logger.info("Backup complete. Private storage retention: daily=14, weekly=8")


if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    backup()
