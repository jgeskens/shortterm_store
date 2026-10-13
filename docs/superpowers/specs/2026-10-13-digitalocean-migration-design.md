# Move Shortterm Store from AWS to a DigitalOcean VPS

Date: 2026-10-13

## Goal

Run shortterm.store entirely on Jef's DigitalOcean droplet and remove every AWS
dependency (Lambda/Zappa, RDS, S3, SES, ACM). Existing stores, uploads and
embedded image links must keep working after cutover.

## Current state

- Django 6.0 on Lambda via Zappa (`zappa_settings.json`, stages `dev` and `test`).
- Postgres on RDS.
- Uploads in S3 bucket `geskens-shorttermstore-data`, keyed `<item_guid>/<filename>`.
  Browser uploads go directly to S3 via presigned POST; downloads redirect to presigned GET URLs.
- SES SMTP is configured but unused (no code sends mail, `ADMINS` is unset).
- Daily Zappa event runs `store.jobs.clean_up_empty_stores`.

## Decisions

| Concern | Choice |
|---|---|
| Host | `node3.geskens.be`, reachable as `root@node3.geskens.be` over SSH with public-key auth |
| Orchestration | Docker Compose |
| File storage | Local disk at `/mnt/volume-ams3-01/shortterm_store/storage` |
| HTTP / TLS | Existing host nginx, certbot certificates |
| Database | `postgres:16` service in the compose project (same image the other projects on the host already use) |
| Email | Dropped (unused) |
| Deploy | `deploy.sh`: rsync to droplet, then `docker compose up -d --build` over SSH |
| Backups | Out of scope |
| DNS | Managed by Jef. The `shortterm.store` zone is on Route 53; `new.shortterm.store` already points to node3 (188.166.33.64). Moving the zone off Route 53 is a manual step by Jef, outside this spec |

## Runtime layout

```
host nginx (TLS via certbot)
 ├─ /             → proxy_pass http://127.0.0.1:8400
 └─ /_protected/  → internal; alias /mnt/volume-ams3-01/shortterm_store/storage/

docker compose project at /root/shortterm_store (same convention as the other projects on node3)
 ├─ app   python:3.12-slim + gunicorn, published on 127.0.0.1:8400 only (8000 is taken by queuebuddy)
 │        bind mount storage/ → /storage
 │        entrypoint: migrate, collectstatic, then gunicorn (2 sync workers, --timeout 300)
 ├─ db    postgres:16, data in named volume shortterm_store_pgdata (like <project>_pgdata elsewhere)
 └─ cron  same image as app; shell loop running `manage.py cleanup_empty_stores` every 24h (idle cost: one `sh` process)
```

- Admin static files are served by whitenoise; nginx has no static location.
- Secrets live in `.env` next to `docker-compose.yml` on the server and are never rsynced.
- nginx must be able to read the storage directory (the app container's user and
  file permissions are chosen so files are world-readable or group-readable by `www-data`).
- `client_max_body_size 5G` in the nginx vhost, matching the old S3 presigned POST
  limit (the largest existing upload is 3.42 GiB). nginx keeps its default request
  buffering so slow uploads do not occupy gunicorn workers.
- The vhost sets `client_body_temp_path /mnt/volume-ams3-01/shortterm_store/nginx_body`:
  the default nginx temp dir is on the root disk, which has only ~9 GB free.
- The vhost follows the layout of the existing ones on the host (`/etc/nginx/sites-enabled/<domain>`,
  port-80 server with `/.well-known/acme-challenge/` served from `/var/www/certbot` and a redirect,
  port-443 server with certbot certificates).

## Host facts (checked 2026-10-13)

- Ubuntu 22.04, 1 vCPU, 957 MiB RAM plus 2 GiB swap. Eleven containers already run
  (five projects, each with its own Postgres), about 240 MiB RAM available. Hence 2 gunicorn
  workers and no extra long-running Python processes.
- Docker 29.8, Compose v5.5, nginx 1.18, certbot 1.21, rsync present.
- Root disk: 25 GB, 9.3 GB free. Docker images and build cache live there.
- `/mnt/volume-ams3-01`: 496 GB, 442 GB free; `shortterm_store/` does not exist yet.
- Ports in use on localhost: 8000, 8080, 8090, 8100, 8200, 8300.

## Code changes

### Upload protocol (frontend unchanged)

The frontend's three-step flow stays the same: fetch `{url, fields}` from
`generate-upload-params/`, POST `fields` + `file` as multipart to `url`, then
register via `upload/`.

- `generate-upload-params/` returns
  `{"url": "/<item_guid>/upload/receive/", "fields": {"key": "<item_guid>/<filename>", "token": <signed>}}`.
  The token is `django.core.signing.dumps(key, salt="upload")`.
- New view `upload_receive` at `<item_guid>/upload/receive/` (POST, `csrf_exempt`):
  - `signing.loads(token, salt="upload", max_age=3600)` must equal `key`; otherwise 403.
  - `key` must be `<item_guid>/<basename>` where basename is `os.path.basename(filename)`,
    non-empty, not `.` or `..`; otherwise 400.
  - Writes the uploaded file to `STORAGE_ROOT/<key>`, creating the item directory,
    overwriting an existing file (S3 semantics). Returns 204.
- `FILE_UPLOAD_TEMP_DIR` points to `STORAGE_ROOT/.tmp` so the final move is a rename on the same filesystem.
- `upload/` (registration) is unchanged. `Upload.url` keeps whatever the client sends; it is not used for serving.

### Download

`upload_download` keeps the password check, then returns an empty response with
`X-Accel-Redirect: /_protected/<urlquoted key>`. nginx streams the file and sets
the content type from the extension.

### Delete

`upload_delete` removes `STORAGE_ROOT/<key>` (missing file is not an error) and
removes the item directory if empty, then deletes the DB row.

### Cleanup job

New management command `cleanup_empty_stores` calling `Item.objects.cleanup_empty()`.
`store/jobs.py` is removed. Only stores without uploads are deleted, so no files are orphaned.

### Removals and settings

- Remove `store/s3.py`, `Upload.presigned_url`, boto3 imports, zappa from requirements, `zappa_settings.json` usage.
- Remove the email settings block.
- Add `gunicorn` and `whitenoise` to requirements.
- New settings: `STORAGE_ROOT` (env, default `BASE_DIR / 'storage'`), `STATIC_ROOT`,
  `CSRF_TRUSTED_ORIGINS` (env), `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`.
- Remove `UPLOAD_BUCKET`.

### New files

- `Dockerfile`, `docker-compose.yml`, `docker/entrypoint.sh`
- `deploy/nginx.conf` (vhost for `new.shortterm.store` / `shortterm.store`, installed manually once)
- `deploy.sh` (target `root@node3.geskens.be:/root/shortterm_store`, overridable via `DEPLOY_HOST`; rsync excluding `.git`, `.env`, `venv`, `.idea`, `__pycache__`, `storage`; then remote `docker compose up -d --build`)
- `.dockerignore`
- Update `dot_env_example.env` with the new variables.

## Secrets

Keep the current production `SECRET_KEY`: `get_item_password_token` derives the
`?t=` tokens embedded in image links of password-protected stores from it.
Changing it would break those links. A new Postgres password is generated for the
compose database.

## File migration

Measured on 2026-10-13 (profile `geskens`): 825 objects, 19.74 GiB, all `STANDARD`
storage class, versioning never enabled. Largest objects: 3.42, 1.27, 0.98, 0.69, 0.57 GiB.
This fits comfortably in the 450 GB volume; S3 egress is about $1.80.

1. **No AWS credentials on the server.** `deploy/s3_manifest.py` runs on Jef's laptop
   (profile `geskens`), lists the bucket and prints one JSON line per object with its key,
   size, LastModified and a presigned GET URL valid for 6 hours.
2. **Copy:** the manifest is piped over SSH into `deploy/fetch_s3_manifest.py` (stdlib only)
   on the server, which downloads every object to `<storage>/<key>`. Files that already exist
   with the same size and an mtime at or after LastModified are skipped. Keys map 1:1 to paths.
3. **Permissions:** chown the tree to the app container's UID and make it readable by
   `www-data` (same rules as files written by `upload_receive`).
4. **Verification:** a management command `verify_storage` that reports
   - total file count and bytes under `STORAGE_ROOT` (compared by hand with the S3 numbers above / a fresh `aws s3 ls --summarize`),
   - `Upload` rows whose file is missing on disk,
   - files on disk with no `Upload` row (reported only, not deleted).
5. **Delta at cutover:** rerun the manifest pipe; it only transfers new or changed
   objects, keeping the downtime window short. Rerun permissions and `verify_storage`.

## Migration and cutover

1. Provision: create storage/postgres dirs on the volume, write `.env`, run `deploy.sh`,
   install the nginx vhost for `new.shortterm.store`, run certbot.
2. Initial data copy:
   - Files: steps 1–4 of *File migration*.
   - `pg_dump` from RDS (Postgres 15.18, publicly reachable from node3) with the
     `postgres:16` client image, restore into the compose `db`.
3. Verify on `new.shortterm.store`: open existing stores (including password-protected ones
   with embedded images), upload via uploader and via paste, download, delete, carousel, mp3 playback.
4. Cutover: final `pg_dump`/restore and step 5 of *File migration* for the delta, add `shortterm.store`
   to the nginx vhost, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, repoint DNS, certbot for `shortterm.store`.
5. After about a week: `zappa undeploy dev` and `test`, delete the RDS database, both S3 buckets,
   the ACM certificates and the SES SMTP IAM user. Remove `new.shortterm.store`.

## Testing

Django tests with a temporary `STORAGE_ROOT` (via `override_settings` + tempdir):

- `generate-upload-params` returns the receive URL and a key/token pair; locked item returns 403.
- `upload_receive` stores the file at the right path; rejects a missing, tampered, expired
  or mismatched-key token; rejects path-traversal filenames.
- `upload_download` returns `X-Accel-Redirect` with the quoted key; 403 when locked; `?t=` token works.
- `upload_delete` removes the file and the row.
- `cleanup_empty_stores` command deletes empty items only.
- `verify_storage` command reports missing files and orphan files correctly.

## Out of scope

Backups, email, DNS hosting changes, CI/CD, frontend changes.
