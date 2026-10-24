#!/usr/bin/env python3
"""B2C-only PostgreSQL/artifact checkpoint and isolated restore drill.

No source DB/object writes, no credential values in manifests or process argv.
This is a local checkpoint; it does not replace encrypted off-host 3-2-1 backup.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-p", "b2c", "--env-file", str(ROOT / ".env"),
           "-f", str(ROOT / "docker/compose.yml"),
           "-f", str(ROOT / "docker/compose.observability.yml")]

OBJECT_EXPORT = r"""
import hashlib, json, sys, tarfile
from b2c_core.config import Settings
from b2c_core.artifacts import ArtifactStore
s = ArtifactStore.from_settings(Settings())
manifest = []
class HashReader:
    def __init__(self, body): self.body, self.hash = body, hashlib.sha256()
    def read(self, size=-1):
        data = self.body.read(size); self.hash.update(data); return data
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
    for page in s.s3.get_paginator("list_objects_v2").paginate(Bucket=s.bucket):
        for entry in page.get("Contents", []):
            key = entry["Key"]
            obj = s.s3.get_object(Bucket=s.bucket, Key=key)
            reader = HashReader(obj["Body"])
            info = tarfile.TarInfo("objects/" + key)
            info.size = obj["ContentLength"]
            archive.addfile(info, reader)
            manifest.append({"key":key, "size":info.size, "sha256":reader.hash.hexdigest()})
    data = json.dumps(manifest, sort_keys=True).encode()
    import io
    info = tarfile.TarInfo("object-manifest.json"); info.size = len(data)
    archive.addfile(info, io.BytesIO(data))
"""

OBJECT_RESTORE = r"""
import hashlib, io, json, sys, tarfile, uuid
from b2c_core.config import Settings
from b2c_core.artifacts import ArtifactStore
s = ArtifactStore.from_settings(Settings())
bucket = "b2c-restore-check-" + uuid.uuid4().hex
s.s3.create_bucket(Bucket=bucket)
count = 0
try:
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
        seen = {}
        for member in archive:
            if member.name == "object-manifest.json":
                manifest = json.load(archive.extractfile(member)); continue
            if not member.isfile() or not member.name.startswith("objects/"):
                raise ValueError("invalid object archive")
            key = member.name[len("objects/"):]
            body = archive.extractfile(member).read()
            s.s3.put_object(Bucket=bucket, Key=key, Body=body)
            reread = s.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            digest = hashlib.sha256(reread).hexdigest()
            if reread != body: raise ValueError("object readback mismatch")
            seen[key] = {"size": len(reread), "sha256": digest}; count += 1
        if len(manifest) != len(seen): raise ValueError("object count mismatch")
        for item in manifest:
            if seen[item["key"]] != {"size":item["size"], "sha256":item["sha256"]}:
                raise ValueError("object hash mismatch")
    restored = ArtifactStore(s.s3, bucket)
    refs = 0
    from b2c_contracts import ArtifactRef
    for key in seen:
        if key.endswith("/_manifest.json"):
            ref = ArtifactRef.model_validate_json(s.s3.get_object(Bucket=bucket, Key=key)["Body"].read())
            ref = ref.model_copy(update={"uri": ref.uri.replace("s3://" + s.bucket + "/", "s3://" + bucket + "/", 1)})
            for filename in restored.list_files(ref): restored.read(ref, filename)
            refs += 1
    print(json.dumps({"objects_readback":count, "artifact_refs_readable":refs}))
finally:
    # This generated bucket is the only deletion target; source bucket is never modified.
    for page in s.s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for item in page.get("Contents",[]): s.s3.delete_object(Bucket=bucket, Key=item["Key"])
    s.s3.delete_bucket(Bucket=bucket)
"""

def command(args, **kwargs):
    return subprocess.run(args, check=True, stderr=subprocess.PIPE, **kwargs)

def capture(args):
    return command(args, stdout=subprocess.PIPE).stdout

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for data in iter(lambda: f.read(1024 * 1024), b""): digest.update(data)
    return digest.hexdigest()

def require_source():
    command(["findmnt", "-M", "/mnt/wsl/b2cdata"], stdout=subprocess.DEVNULL)
    if not Path("/mnt/wsl/b2cdata/.b2c-data-disk").is_file(): raise ValueError("B2C disk marker absent")
    command(COMPOSE + ["config", "-q"])
    ids = {}
    for service in ("postgres", "worker"):
        cid = capture(COMPOSE + ["ps", "-q", service]).decode().strip()
        if not cid or "\n" in cid: raise ValueError("required B2C service unavailable")
        inspect = json.loads(capture(["docker", "inspect", cid]))[0]
        if inspect["Config"]["Labels"].get("com.docker.compose.project") != "b2c":
            raise ValueError("container project mismatch")
        if not inspect["State"]["Running"]: raise ValueError("container stopped")
        ids[service] = cid
    return ids

def db_command(cid, tool, extra):
    # Credentials stay inside the existing container environment.
    return ["docker", "exec", cid, "sh", "-c",
            'exec ' + tool + ' -U "$POSTGRES_USER" -d "$POSTGRES_DB" ' + extra]

def table_counts(cid, user=None, database=None):
    sql = "SELECT COALESCE(json_object_agg(tablename, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, tablename), false, true, '')))[1]::text::bigint), '{}'::json) FROM pg_tables WHERE schemaname='public';"
    if user is None:
        args = db_command(cid, "psql", "-Atc " + __import__("shlex").quote(sql))
    else:
        args = ["docker","exec",cid,"psql","-U",user,"-d",database,"-Atc",sql]
    return json.loads(capture(args))

def snapshot(destination):
    ids = require_source()
    dest = destination.resolve()
    source = Path("/mnt/wsl/b2cdata").resolve()
    if dest == source or source in dest.parents: raise ValueError("backup destination must be outside the B2C data disk")
    dest.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        counts_before = table_counts(ids["postgres"])
        dump = dest / "b2c.pgdump"
        with dump.open("wb") as f:
            command(db_command(ids["postgres"], "pg_dump", "--format=custom --no-owner --no-acl"), stdout=f)
        counts_after = table_counts(ids["postgres"])
        if counts_before != counts_after: raise RuntimeError("database changed during snapshot; retry in a quiet window")
        objects = dest / "artifacts.tar"
        with objects.open("wb") as f:
            command(["docker", "exec", "-i", ids["worker"], "python", "-c", OBJECT_EXPORT], stdout=f)
        manifest = {"format":1, "created_at_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "table_counts":counts_before,
                    "scope":"B2C application database and artifacts bucket; local checkpoint",
                    "limitations":["not a coordinated DB/object transaction", "not WAL/PITR", "not off-host 3-2-1", "excludes Temporal/Langfuse databases, models, and external secret files"],
                    "files":{p.name:{"bytes":p.stat().st_size,"sha256":sha(p)} for p in (dump,objects)}}
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        for path in dest.iterdir(): path.chmod(0o600)
        print(json.dumps({"snapshot":str(dest), "files":manifest["files"]}))
    except BaseException:
        # Preserve failed partial files for diagnosis; no success manifest is written.
        raise

def restore_check(snapshot_path):
    ids = require_source()
    path = snapshot_path.resolve()
    manifest = json.loads((path / "manifest.json").read_text())
    for name in ("b2c.pgdump","artifacts.tar"):
        info = manifest["files"][name]; file = path / name
        if file.stat().st_size != info["bytes"] or sha(file) != info["sha256"]:
            raise ValueError("checkpoint integrity mismatch")
    source_info = json.loads(capture(["docker", "inspect", ids["postgres"]]))[0]
    image = source_info["Config"]["Image"]
    name = "b2c-restore-check-" + uuid.uuid4().hex
    result = {"snapshot_sha256":sha(path/"manifest.json")}
    command(["docker", "run", "-d", "--name", name, "--label", "com.docker.compose.project=b2c",
             "--label", "b2c.restore-drill=true", "--network", "none", "--memory", "512m", "--cpus", "1",
             "--tmpfs", "/var/lib/postgresql/data:rw,size=2g", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
             "-e", "POSTGRES_DB=b2c_restore", image], stdout=subprocess.DEVNULL)
    try:
        for _ in range(60):
            if subprocess.run(["docker","exec",name,"pg_isready","-U","postgres","-d","b2c_restore"],
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0: break
            time.sleep(1)
        else: raise RuntimeError("restore PostgreSQL did not become ready")
        with (path/"b2c.pgdump").open("rb") as f:
            command(["docker","exec","-i",name,"pg_restore","-U","postgres","-d","b2c_restore",
                     "--exit-on-error","--no-owner","--no-acl"], stdin=f, stdout=subprocess.DEVNULL)
        table_count = capture(["docker","exec",name,"psql","-U","postgres","-d","b2c_restore","-Atc",
                              "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"]).decode().strip()
        result["restored_public_tables"] = int(table_count)
        restored_counts = table_counts(name, "postgres", "b2c_restore")
        if restored_counts != manifest["table_counts"]: raise ValueError("database row count mismatch")
        result["verified_database_rows"] = sum(restored_counts.values())
        result["per_table_counts_matched"] = True
        with (path/"artifacts.tar").open("rb") as f:
            objects = capture_restore(ids["worker"], f)
        result.update(objects)
        result["status"] = "PASS"
        (path/"restore-check.json").write_text(json.dumps(result,indent=2)+"\n")
        (path/"restore-check.json").chmod(0o600)
        print(json.dumps(result))
    finally:
        command(["docker","rm","-f",name], stdout=subprocess.DEVNULL)

def capture_restore(worker, archive):
    return json.loads(command(["docker","exec","-i",worker,"python","-c",OBJECT_RESTORE],
                              stdin=archive,stdout=subprocess.PIPE).stdout)

def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["snapshot","restore-check"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.action == "snapshot": snapshot(args.path)
    else: restore_check(args.path)

if __name__ == "__main__":
    main()
