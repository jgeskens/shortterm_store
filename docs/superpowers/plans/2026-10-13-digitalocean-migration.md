# DigitalOcean Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run shortterm.store on `node3.geskens.be` with Docker Compose, local-disk file storage and host nginx, and remove every AWS dependency.

**Architecture:** The Django app runs under gunicorn in a container next to a `postgres:16` container. The browser keeps its existing three-step upload protocol, but the upload target becomes a Django view that checks a signed token and writes to a bind-mounted directory. Downloads are authorized by Django and streamed by host nginx through `X-Accel-Redirect`.

**Tech Stack:** Django 6.0.3, Python 3.12, gunicorn, whitenoise, PostgreSQL 16, Docker Compose v5, nginx 1.18, certbot, rsync.

**Spec:** `docs/superpowers/specs/2026-10-13-digitalocean-migration-design.md`

## Global Constraints

- Host: `root@node3.geskens.be`, SSH public-key auth. Follow the host's conventions: code and `.env` in `/root/shortterm_store` (like `/root/flushtime`, `/root/queuebuddy`, ...), Postgres in the named volume `shortterm_store_pgdata`. Only bulk data goes on the volume: `/mnt/volume-ams3-01/shortterm_store/storage` and `/mnt/volume-ams3-01/shortterm_store/nginx_body`.
- App published on `127.0.0.1:8400` only (8000, 8080, 8090, 8100, 8200, 8300 are taken).
- Gunicorn: 2 sync workers, `--timeout 300`. No extra long-running Python processes (957 MiB RAM box).
- Database image `postgres:16`. Database, user: `shorttermstore`.
- Storage key format is unchanged: `<item_guid>/<filename>`; stored at `STORAGE_ROOT/<key>`.
- nginx: `client_max_body_size 5G`; `client_body_temp_path /mnt/volume-ams3-01/shortterm_store/nginx_body`; internal location `/_protected/` aliased to the storage dir.
- Upload token: `django.core.signing`, salt `store.upload`, max age 24 h (checked after nginx has buffered the whole upload).
- Keep the current production `SECRET_KEY` (from `zappa_settings.json`, stage `dev`): it derives the `?t=` tokens embedded in image links.
- Files and dirs written to storage: mode `0644` / `0755`, owned by UID/GID 33 (`www-data` on host and in the container).
- No email, no backups, no CI, no frontend changes.
- `zappa_settings.json` and `.env` must never be committed, rsynced or copied into the image.
- Run the Django test suite inside Docker: `docker compose run --rm app python manage.py test store` (there is no local Postgres; the local virtualenv has Django 4.2, not 6.0).

## Review Focus

1. **Registering a key that belongs to another store.** `upload/` currently trusts the client-sent `key`, so a client could register `<other_guid>/secret.png` and download it without that store's password. Expected: the server always rebuilds the key from its own item guid and the basename. Test in Task 3.
2. **Filenames with spaces, unicode, `#`, `?` or `%`** (legacy S3 keys contain all sorts). Expected: download works; `X-Accel-Redirect` is percent-encoded. Test in Task 4.
3. **Filenames with path parts (`../x`, `a/b.png`, `C:\x\y.png`).** Expected: stored under the item's own dir as the basename; never outside `STORAGE_ROOT`. Tests in Tasks 2 and 3.
4. **Upload rows whose file is missing on disk** (incomplete migration, failed upload). Expected: 404, not a 500 or an nginx error page with a misleading status. Test in Task 4.
5. **Large uploads that Django spools to a temp file** (anything over 2.5 MB). Expected: the file is moved into place with mode 0644 and the temp file is gone. Test in Task 2.

---

## File Structure

| Path | Responsibility |
|---|---|
| `Dockerfile` | App image: deps, collectstatic, runs as `www-data` |
| `.dockerignore` | Keeps secrets, VCS and data out of the build context |
| `docker-compose.yml` | `app`, `db`, `cron` services; storage path comes from `.env` interpolation, Postgres in named volume `pgdata` |
| `docker/start.sh` | Container start: ensure temp dir, migrate, exec gunicorn |
| `shortterm_store/settings.py` | Env-driven settings incl. `STORAGE_ROOT`, whitenoise, proxy SSL header |
| `store/storage.py` | Everything about files on disk: key building, safe paths, save, delete, accel header |
| `store/testing.py` | `TempStorageMixin` for tests |
| `store/views.py` | Upload params, receive, register, download, delete |
| `store/urls.py` | Adds `upload/receive/` |
| `store/management/commands/cleanup_empty_stores.py` | Daily cleanup (replaces `store/jobs.py`) |
| `store/management/commands/verify_storage.py` | Compares DB upload rows with files on disk |
| `store/test_storage.py`, `store/test_uploads.py`, `store/test_commands.py` | New tests |
| `deploy/s3_manifest.py` | Laptop-side: list bucket and presign URLs |
| `deploy/fetch_s3_manifest.py`, `deploy/test_fetch_s3_manifest.py` | Server-side downloader and its tests |
| `deploy/nginx.conf` | Host nginx vhost |
| `deploy.sh` | rsync + `docker compose up -d --build` |
| `dot_env_example.env` | Documents the server `.env` |

Removed: `store/s3.py`, `store/jobs.py`.

---

### Task 1: Container scaffolding and settings

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker/start.sh`
- Modify: `requirements.txt`, `shortterm_store/settings.py`, `.gitignore`

**Interfaces:**
- Produces: setting `STORAGE_ROOT` (a `pathlib.Path`), `FILE_UPLOAD_TEMP_DIR`; compose services `app`, `db`; the test command `docker compose run --rm app python manage.py test store`.

- [ ] **Step 1: Replace `requirements.txt`**

boto3 used to come in through zappa; it is pinned explicitly here until Task 4 removes S3.

```
Django==6.0.3
django-environ
psycopg2-binary
gunicorn
whitenoise
boto3
```

- [ ] **Step 2: Create `.dockerignore`**

```
.git
.env
zappa_settings.json
venv
.idea
.ropeproject
.python-version
.DS_Store
**/__pycache__
**/*.pyc
storage
staticfiles
docs
```

- [ ] **Step 3: Append to `.gitignore`**

```
/storage
/staticfiles
```

- [ ] **Step 4: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN SECRET_KEY=collectstatic DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    python manage.py collectstatic --noinput

USER www-data
EXPOSE 8000
CMD ["sh", "docker/start.sh"]
```

- [ ] **Step 5: Create `docker/start.sh`**

```sh
#!/bin/sh
set -e

mkdir -p "$STORAGE_ROOT/.tmp"
python manage.py migrate --noinput
exec gunicorn shortterm_store.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 300 \
    --access-logfile -
```

- [ ] **Step 6: Create `docker-compose.yml`**

Values are interpolated from `.env` in the project dir (on the server that is the real config; locally the developer's `.env` supplies `SECRET_KEY`/`DEBUG` and the rest falls back to defaults). No `env_file:` on purpose: the local `.env` contains AWS settings that must not leak into the container.

```yaml
services:
  app:
    build: .
    restart: unless-stopped
    environment:
      SECRET_KEY: ${SECRET_KEY:-dev-insecure-key}
      DEBUG: ${DEBUG:-False}
      ALLOWED_HOSTS: ${ALLOWED_HOSTS:-localhost,127.0.0.1}
      CSRF_TRUSTED_ORIGINS: ${CSRF_TRUSTED_ORIGINS:-}
      DATABASE_URL: postgres://shorttermstore:${POSTGRES_PASSWORD:-shorttermstore}@db:5432/shorttermstore
      STORAGE_ROOT: /storage
    volumes:
      - ${STORAGE_DIR:-./storage}:/storage
    ports:
      - "127.0.0.1:${APP_PORT:-8400}:8000"
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: shorttermstore
      POSTGRES_USER: shorttermstore
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-shorttermstore}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U shorttermstore -d shorttermstore"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  pgdata:
```

- [ ] **Step 7: Update `shortterm_store/settings.py`**

Replace `ALLOWED_HOSTS = env('ALLOWED_HOSTS', default=[])` with:

```python
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])
CSRF_TRUSTED_ORIGINS = [origin for origin in env.list('CSRF_TRUSTED_ORIGINS', default=[]) if origin]
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
```

In `MIDDLEWARE`, insert directly after `'django.middleware.security.SecurityMiddleware',`:

```python
    'whitenoise.middleware.WhiteNoiseMiddleware',
```

Delete the line `USE_L10N = True` (removed in Django 5.0).

Replace `STATIC_URL = '/static/'` with:

```python
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}
```

Replace everything from `UPLOAD_BUCKET = env('UPLOAD_BUCKET', default='')` to the end of the file (the email block) with:

```python
UPLOAD_BUCKET = env('UPLOAD_BUCKET', default='')

# Uploaded files

STORAGE_ROOT = Path(env('STORAGE_ROOT', default=str(BASE_DIR / 'storage')))
# On the same filesystem as STORAGE_ROOT, so moving a finished upload into place is a rename.
FILE_UPLOAD_TEMP_DIR = str(STORAGE_ROOT / '.tmp')
```

- [ ] **Step 8: Run the existing tests in the container**

The current code still presigns S3 URLs; dummy credentials let botocore sign offline.

Run: `docker compose run --rm -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test -e AWS_DEFAULT_REGION=eu-central-1 -e UPLOAD_BUCKET=test-bucket app python manage.py test store`

Expected: `Ran 4 tests ... OK`. If Django 6 reports a deprecation-related error, fix the setting it names before continuing.

- [ ] **Step 9: Smoke-test the stack**

Run:
```bash
docker compose up -d --build
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' -H 'Host: localhost' http://127.0.0.1:8400/
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: localhost' http://127.0.0.1:8400/static/admin/css/base.css
docker compose down
```
Expected: `302 http://127.0.0.1:8400/<uuid>/` (or a localhost URL) and `200`.

- [ ] **Step 10: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml docker/start.sh requirements.txt shortterm_store/settings.py .gitignore
git commit -m "Run the app with Docker Compose, gunicorn and whitenoise"
```

---

### Task 2: Storage module

**Files:**
- Create: `store/storage.py`, `store/testing.py`, `store/test_storage.py`

**Interfaces:**
- Consumes: `settings.STORAGE_ROOT` (Task 1).
- Produces (all in `store.storage`):
  - `class InvalidKey(ValueError)`
  - `clean_filename(filename: str) -> str`: basename, raises `InvalidKey` for empty, `.`, `..`
  - `key_for(item_guid, filename: str) -> str`: `f'{item_guid}/{clean_filename(filename)}'`
  - `path_for_key(key: str) -> pathlib.Path`: absolute path inside `STORAGE_ROOT`, raises `InvalidKey` otherwise
  - `save(key: str, uploaded_file) -> pathlib.Path`
  - `delete(key: str) -> None`
  - `accel_redirect(key: str) -> str`: `'/_protected/' + quote(key)`
  - `content_type(key: str) -> str`
- Produces (in `store.testing`): `TempStorageMixin` (sets `self.storage_root: Path` containing an empty `.tmp/`, overrides `STORAGE_ROOT` and `FILE_UPLOAD_TEMP_DIR`).

- [ ] **Step 1: Create `store/testing.py`**

```python
import tempfile
from pathlib import Path

from django.test import override_settings


class TempStorageMixin:
    """Point STORAGE_ROOT at a fresh temporary directory for each test."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage_root = Path(tmp.name)
        (self.storage_root / '.tmp').mkdir()
        override = override_settings(
            STORAGE_ROOT=self.storage_root,
            FILE_UPLOAD_TEMP_DIR=str(self.storage_root / '.tmp'),
        )
        override.enable()
        self.addCleanup(override.disable)
```

- [ ] **Step 2: Write the failing tests in `store/test_storage.py`**

```python
import os
import stat
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.test import SimpleTestCase

from . import storage
from .testing import TempStorageMixin


class CleanFilenameTest(SimpleTestCase):
    def test_keeps_plain_names(self):
        self.assertEqual(storage.clean_filename('photo 1.png'), 'photo 1.png')
        self.assertEqual(storage.clean_filename('liedje é#?%.mp3'), 'liedje é#?%.mp3')

    def test_strips_directories(self):
        self.assertEqual(storage.clean_filename('a/b.png'), 'b.png')
        self.assertEqual(storage.clean_filename('../../etc/passwd'), 'passwd')
        self.assertEqual(storage.clean_filename('C:\\Users\\x\\y.png'), 'y.png')

    def test_rejects_empty_and_dots(self):
        for name in ('', '.', '..', 'a/', '  '):
            with self.subTest(name=name):
                with self.assertRaises(storage.InvalidKey):
                    storage.clean_filename(name)

    def test_key_for(self):
        guid = uuid.uuid4()
        self.assertEqual(storage.key_for(guid, '../x.png'), f'{guid}/x.png')


class PathForKeyTest(TempStorageMixin, SimpleTestCase):
    def test_inside_root(self):
        path = storage.path_for_key('abc/file.txt')
        self.assertEqual(path, self.storage_root.resolve() / 'abc' / 'file.txt')

    def test_rejects_escape_and_root(self):
        for key in ('../outside.txt', 'abc/../../outside.txt', '', '.'):
            with self.subTest(key=key):
                with self.assertRaises(storage.InvalidKey):
                    storage.path_for_key(key)


class SaveDeleteTest(TempStorageMixin, SimpleTestCase):
    def test_save_in_memory_file(self):
        path = storage.save('abc/hello.txt', SimpleUploadedFile('hello.txt', b'hello'))
        self.assertEqual(path.read_bytes(), b'hello')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_save_overwrites(self):
        storage.save('abc/hello.txt', SimpleUploadedFile('hello.txt', b'one'))
        path = storage.save('abc/hello.txt', SimpleUploadedFile('hello.txt', b'two'))
        self.assertEqual(path.read_bytes(), b'two')
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ['hello.txt'])

    def test_save_temporary_file_moves_it(self):
        uploaded = TemporaryUploadedFile('big.bin', 'application/octet-stream', 5, None)
        uploaded.write(b'12345')
        uploaded.flush()
        temp_path = uploaded.temporary_file_path()

        path = storage.save('abc/big.bin', uploaded)

        self.assertEqual(path.read_bytes(), b'12345')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertFalse(os.path.exists(temp_path))
        uploaded.close()  # must not raise although the temp file is gone

    def test_delete_removes_file_and_empty_dir(self):
        path = storage.save('abc/hello.txt', SimpleUploadedFile('hello.txt', b'x'))
        storage.delete('abc/hello.txt')
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())
        self.assertTrue(self.storage_root.exists())

    def test_delete_keeps_dir_with_other_files(self):
        storage.save('abc/a.txt', SimpleUploadedFile('a.txt', b'a'))
        storage.save('abc/b.txt', SimpleUploadedFile('b.txt', b'b'))
        storage.delete('abc/a.txt')
        self.assertTrue((self.storage_root / 'abc' / 'b.txt').exists())

    def test_delete_missing_is_fine(self):
        storage.delete('abc/nope.txt')


class AccelTest(SimpleTestCase):
    def test_accel_redirect_quotes(self):
        self.assertEqual(
            storage.accel_redirect('abc/my file #1?.png'),
            '/_protected/abc/my%20file%20%231%3F.png',
        )
        self.assertEqual(storage.accel_redirect('abc/é.mp3'), '/_protected/abc/%C3%A9.mp3')

    def test_content_type(self):
        self.assertEqual(storage.content_type('abc/a.PNG'), 'image/png')
        self.assertEqual(storage.content_type('abc/a.mp3'), 'audio/mpeg')
        self.assertEqual(storage.content_type('abc/noext'), 'application/octet-stream')
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `docker compose run --rm --build app python manage.py test store.test_storage`
Expected: errors with `ImportError: cannot import name 'storage'`.

- [ ] **Step 4: Implement `store/storage.py`**

```python
"""Uploaded files on local disk, stored at STORAGE_ROOT/<item_guid>/<filename>."""
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from django.conf import settings

ACCEL_PREFIX = '/_protected/'
FILE_MODE = 0o644


class InvalidKey(ValueError):
    pass


def clean_filename(filename):
    name = os.path.basename((filename or '').replace('\\', '/')).strip()
    if name in ('', '.', '..'):
        raise InvalidKey(f'Invalid filename: {filename!r}')
    return name


def key_for(item_guid, filename):
    return f'{item_guid}/{clean_filename(filename)}'


def _root():
    return Path(settings.STORAGE_ROOT).resolve()


def path_for_key(key):
    root = _root()
    path = (root / key).resolve()
    if path == root or not path.is_relative_to(root):
        raise InvalidKey(f'Invalid key: {key!r}')
    return path


def save(key, uploaded_file):
    path = path_for_key(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(uploaded_file, 'temporary_file_path'):
        shutil.move(uploaded_file.temporary_file_path(), path)
    else:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            for chunk in uploaded_file.chunks():
                out.write(chunk)
        os.replace(out.name, path)
    os.chmod(path, FILE_MODE)
    return path


def delete(key):
    path = path_for_key(key)
    path.unlink(missing_ok=True)
    if path.parent != _root():
        try:
            path.parent.rmdir()
        except OSError:
            pass


def accel_redirect(key):
    return ACCEL_PREFIX + quote(key)


def content_type(key):
    return mimetypes.guess_type(key)[0] or 'application/octet-stream'
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `docker compose run --rm --build app python manage.py test store.test_storage`
Expected: all tests OK.

- [ ] **Step 6: Commit**

```bash
git add store/storage.py store/testing.py store/test_storage.py
git commit -m "Add local-disk storage module"
```

---

### Task 3: Upload endpoints on local storage

**Files:**
- Modify: `store/views.py` (`generate_upload_params`, `upload`; add `upload_receive`), `store/urls.py`
- Create: `store/test_uploads.py`

**Interfaces:**
- Consumes: `storage.key_for`, `storage.save`, `storage.InvalidKey`, `TempStorageMixin` (Task 2).
- Produces: URL name `upload_receive` → `/<item_guid>/upload/receive/`; module constants `views.UPLOAD_TOKEN_SALT = 'store.upload'`, `views.UPLOAD_TOKEN_MAX_AGE = 3600`. Response of `generate-upload-params/`: `{"url": str, "fields": {"key": str, "token": str}}`.

- [ ] **Step 1: Write the failing tests in `store/test_uploads.py`**

```python
import json
import time
import uuid
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from . import views
from .models import Item, Upload
from .testing import TempStorageMixin


class UploadFlowTest(TempStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.guid = str(uuid.uuid4())
        self.item = Item.objects.create(guid=self.guid)

    def get_params(self, filename, **extra):
        response = self.client.post(f'/{self.guid}/generate-upload-params/', {'filename': filename}, **extra)
        return response

    def send_file(self, url, fields, content=b'data', name='file.bin'):
        data = dict(fields)
        data['file'] = SimpleUploadedFile(name, content)
        return self.client.post(url, data)

    def test_full_flow(self):
        params = self.get_params('photo 1.png').json()
        self.assertEqual(params['url'], f'/{self.guid}/upload/receive/')
        self.assertEqual(params['fields']['key'], f'{self.guid}/photo 1.png')

        response = self.send_file(params['url'], params['fields'], b'png-bytes')
        self.assertEqual(response.status_code, 204)
        self.assertEqual((self.storage_root / self.guid / 'photo 1.png').read_bytes(), b'png-bytes')

        response = self.client.post(
            f'/{self.guid}/upload/',
            data=json.dumps({'key': params['fields']['key'], 'url': 'whatever'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Upload.objects.get(item=self.item).key, f'{self.guid}/photo 1.png')

    def test_params_strip_path(self):
        params = self.get_params('../../etc/passwd').json()
        self.assertEqual(params['fields']['key'], f'{self.guid}/passwd')

    def test_params_reject_empty_filename(self):
        self.assertEqual(self.get_params('').status_code, 400)

    def test_params_locked(self):
        self.item.password = make_password('pw')
        self.item.save()
        self.assertEqual(self.get_params('a.png').status_code, 403)
        self.assertEqual(self.get_params('a.png', HTTP_X_ITEM_PASSWORD='pw').status_code, 200)

    def test_receive_works_without_csrf_token(self):
        params = self.get_params('a.txt').json()
        self.client = self.client_class(enforce_csrf_checks=True)
        response = self.send_file(params['url'], params['fields'])
        self.assertEqual(response.status_code, 204)

    def test_receive_overwrites_same_name(self):
        params = self.get_params('a.txt').json()
        self.send_file(params['url'], params['fields'], b'one')
        params = self.get_params('a.txt').json()
        self.send_file(params['url'], params['fields'], b'two')
        self.assertEqual((self.storage_root / self.guid / 'a.txt').read_bytes(), b'two')

    def test_receive_rejects_bad_tokens(self):
        url = f'/{self.guid}/upload/receive/'
        key = f'{self.guid}/a.txt'
        other_guid = str(uuid.uuid4())
        cases = {
            'missing': {'key': key},
            'tampered': {'key': key, 'token': signing.dumps(key, salt=views.UPLOAD_TOKEN_SALT) + 'x'},
            'wrong salt': {'key': key, 'token': signing.dumps(key)},
            'other key': {'key': key, 'token': signing.dumps(f'{self.guid}/b.txt', salt=views.UPLOAD_TOKEN_SALT)},
            'other item': {
                'key': f'{other_guid}/a.txt',
                'token': signing.dumps(f'{other_guid}/a.txt', salt=views.UPLOAD_TOKEN_SALT),
            },
        }
        for name, fields in cases.items():
            with self.subTest(name):
                self.assertEqual(self.send_file(url, fields).status_code, 403)
        self.assertFalse((self.storage_root / self.guid).exists())

    def test_receive_rejects_expired_token(self):
        params = self.get_params('a.txt').json()
        later = time.time() + views.UPLOAD_TOKEN_MAX_AGE + 10
        with mock.patch('django.core.signing.time.time', return_value=later):
            response = self.send_file(params['url'], params['fields'])
        self.assertEqual(response.status_code, 403)

    def test_receive_requires_file(self):
        params = self.get_params('a.txt').json()
        response = self.client.post(params['url'], params['fields'])
        self.assertEqual(response.status_code, 400)

    def test_register_cannot_point_at_other_store(self):
        other_guid = str(uuid.uuid4())
        response = self.client.post(
            f'/{self.guid}/upload/',
            data=json.dumps({'key': f'{other_guid}/secret.png', 'url': 'x'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Upload.objects.get(item=self.item).key, f'{self.guid}/secret.png')

    def test_register_rejects_empty_name(self):
        response = self.client.post(
            f'/{self.guid}/upload/',
            data=json.dumps({'key': f'{self.guid}/', 'url': 'x'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm --build -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test -e AWS_DEFAULT_REGION=eu-central-1 -e UPLOAD_BUCKET=test-bucket app python manage.py test store.test_uploads`
Expected: failures (S3 URL instead of `/upload/receive/`, 404 on the receive URL, `UPLOAD_TOKEN_SALT` missing).

- [ ] **Step 3: Implement the views**

In `store/views.py`, extend the imports:

```python
from django.core import signing
from django.views.decorators.csrf import csrf_exempt
```

and change `from . import s3` to `from . import s3, storage` (s3 goes away in Task 4).

Below the imports add:

```python
UPLOAD_TOKEN_SALT = 'store.upload'
UPLOAD_TOKEN_MAX_AGE = 3600
```

Replace `generate_upload_params` and `upload` with:

```python
@require_POST
def generate_upload_params(request, item_guid):
    item = get_or_create_item(item_guid)
    if not check_item_password(item, request):
        return JsonResponse({'error': 'locked'}, status=403)
    try:
        key = storage.key_for(item.guid, request.POST.get('filename', ''))
    except storage.InvalidKey:
        return JsonResponse({'error': 'invalid filename'}, status=400)
    return JsonResponse({
        'url': reverse('upload_receive', args=(str(item.guid),)),
        'fields': {
            'key': key,
            'token': signing.dumps(key, salt=UPLOAD_TOKEN_SALT),
        },
    })


@csrf_exempt
@require_POST
def upload_receive(request, item_guid):
    # Authorized by the signed token from generate_upload_params, like an S3 presigned POST.
    key = request.POST.get('key', '')
    try:
        signed_key = signing.loads(
            request.POST.get('token', ''), salt=UPLOAD_TOKEN_SALT, max_age=UPLOAD_TOKEN_MAX_AGE
        )
    except signing.BadSignature:
        return JsonResponse({'error': 'invalid token'}, status=403)
    if signed_key != key or key.split('/', 1)[0] != item_guid:
        return JsonResponse({'error': 'invalid token'}, status=403)
    uploaded_file = request.FILES.get('file')
    if uploaded_file is None:
        return JsonResponse({'error': 'no file'}, status=400)
    storage.save(key, uploaded_file)
    return HttpResponse(status=204)


@require_POST
def upload(request, item_guid):
    item = get_or_create_item(item_guid)
    if not check_item_password(item, request):
        return JsonResponse({'error': 'locked'}, status=403)
    data = json.loads(request.body.decode('utf-8'))
    assert 'url' in data and 'key' in data
    # Never trust the client's key: it must live under this item.
    try:
        key = storage.key_for(item.guid, data['key'].rsplit('/', 1)[-1])
    except storage.InvalidKey:
        return JsonResponse({'error': 'invalid filename'}, status=400)
    u, created = models.Upload.objects.get_or_create(item=item, key=key, defaults={'url': data['url']})
    if not created:
        u.modified = now()
        u.save()
    return JsonResponse({
        'url': reverse('upload_download', args=(str(item.guid), str(u.guid)))
    })
```

Note: `rsplit('/', 1)[-1]` then `key_for` (which also splits on `\`) yields the same basename as `generate_upload_params` did for the original filename, so the registered key matches the stored file.

In `store/urls.py`, add above the `'<item_guid>/upload/'` line:

```python
    path('<item_guid>/upload/receive/', views.upload_receive, name='upload_receive'),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm --build -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test -e AWS_DEFAULT_REGION=eu-central-1 -e UPLOAD_BUCKET=test-bucket app python manage.py test store`
Expected: all tests OK, including the old `store.tests`.

- [ ] **Step 5: Commit**

```bash
git add store/views.py store/urls.py store/test_uploads.py
git commit -m "Receive uploads on local disk with signed upload tokens"
```

---

### Task 4: Download and delete from local storage; remove S3

**Files:**
- Modify: `store/views.py` (`upload_download`, `upload_delete`, imports), `store/models.py`, `store/tests.py`, `store/test_uploads.py`, `requirements.txt`, `shortterm_store/settings.py`
- Delete: `store/s3.py`

**Interfaces:**
- Consumes: `storage.path_for_key`, `storage.accel_redirect`, `storage.content_type`, `storage.delete`, `storage.InvalidKey`.
- Produces: download response = HTTP 200, empty body, headers `X-Accel-Redirect: /_protected/<quoted key>` and `Content-Type` from the extension; HTTP 404 when the file is missing.

- [ ] **Step 1: Add the failing tests to `store/test_uploads.py`**

Append:

```python
class DownloadDeleteTest(TempStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.guid = str(uuid.uuid4())
        self.item = Item.objects.create(guid=self.guid)

    def make_upload(self, filename, content=b'x'):
        key = f'{self.guid}/{filename}'
        storage_path = self.storage_root / self.guid / filename
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)
        return Upload.objects.create(item=self.item, key=key, url='x')

    def download_url(self, upload):
        return f'/{self.guid}/upload/{upload.guid}/download/'

    def test_download_uses_accel_redirect(self):
        upload = self.make_upload('song.mp3')
        response = self.client.get(self.download_url(upload))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Accel-Redirect'], f'/_protected/{self.guid}/song.mp3')
        self.assertEqual(response['Content-Type'], 'audio/mpeg')
        self.assertEqual(response.content, b'')

    def test_download_quotes_odd_names(self):
        upload = self.make_upload('mijn foto #1 é?.png')
        response = self.client.get(self.download_url(upload))
        self.assertEqual(
            response['X-Accel-Redirect'],
            f'/_protected/{self.guid}/mijn%20foto%20%231%20%C3%A9%3F.png',
        )

    def test_download_missing_file_is_404(self):
        upload = Upload.objects.create(item=self.item, key=f'{self.guid}/gone.png', url='x')
        self.assertEqual(self.client.get(self.download_url(upload)).status_code, 404)

    def test_download_locked(self):
        self.item.password = make_password('pw')
        self.item.save()
        upload = self.make_upload('a.png')
        self.assertEqual(self.client.get(self.download_url(upload)).status_code, 403)
        self.assertEqual(self.client.get(self.download_url(upload) + '?password=pw').status_code, 200)
        token = views.get_item_password_token(self.item)
        self.assertEqual(self.client.get(self.download_url(upload) + f'?t={token}').status_code, 200)

    def test_delete_removes_file_and_row(self):
        upload = self.make_upload('a.png')
        response = self.client.post(f'/{self.guid}/upload/{upload.guid}/delete/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Upload.objects.filter(pk=upload.pk).exists())
        self.assertFalse((self.storage_root / self.guid / 'a.png').exists())

    def test_delete_with_missing_file(self):
        upload = Upload.objects.create(item=self.item, key=f'{self.guid}/gone.png', url='x')
        response = self.client.post(f'/{self.guid}/upload/{upload.guid}/delete/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Upload.objects.filter(pk=upload.pk).exists())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm --build -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test -e AWS_DEFAULT_REGION=eu-central-1 -e UPLOAD_BUCKET=test-bucket app python manage.py test store.test_uploads.DownloadDeleteTest`
Expected: failures (302 redirects to S3 instead of 200 with `X-Accel-Redirect`).

- [ ] **Step 3: Implement download and delete**

In `store/views.py`, replace `upload_delete` and `upload_download` with:

```python
@require_POST
def upload_delete(request, item_guid, upload_guid):
    item = get_object_or_404(models.Item, guid=item_guid)
    if not check_item_password(item, request):
        return JsonResponse({'error': 'locked'}, status=403)
    upload = get_object_or_404(models.Upload, item=item, guid=upload_guid)
    storage.delete(upload.key)
    upload.delete()
    return HttpResponse('OK')


def upload_download(request, item_guid, upload_guid):
    item = get_object_or_404(models.Item, guid=item_guid)
    if not check_item_password(item, request):
        return HttpResponse('Locked', status=403)
    upload = get_object_or_404(models.Upload, item=item, guid=upload_guid)
    try:
        path = storage.path_for_key(upload.key)
    except storage.InvalidKey:
        raise Http404
    if not path.is_file():
        raise Http404
    # nginx serves the file from its internal /_protected/ location.
    response = HttpResponse(content_type=storage.content_type(upload.key))
    response['X-Accel-Redirect'] = storage.accel_redirect(upload.key)
    return response
```

Remove S3 from `store/views.py`: delete `import boto3` and change `from . import s3, storage` to `from . import storage`.

- [ ] **Step 4: Remove the rest of S3**

- Delete `store/s3.py`.
- In `store/models.py`: delete the `presigned_url` property of `Upload` and the line `from django.core.cache import cache`.
- In `shortterm_store/settings.py`: delete `UPLOAD_BUCKET = env('UPLOAD_BUCKET', default='')`.
- In `requirements.txt`: delete the `boto3` line.

- [ ] **Step 5: Update the old tests in `store/tests.py`**

At the top add `from .testing import TempStorageMixin`. Change the class line and `setUp`:

```python
class PasswordProtectionTest(TempStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.item_guid = str(uuid.uuid4())
        self.item = Item.objects.create(guid=self.item_guid)

    def write_file(self, filename):
        path = self.storage_root / self.item_guid / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x')
```

In `test_protected_endpoints`, after `upload = Upload.objects.create(...)` add `self.write_file('test.txt')`, and replace:

```python
        # Download with password in query string should succeed (redirect to S3)
        response = self.client.get(f'{download_url}?password=testpassword')
        self.assertEqual(response.status_code, 302)
```

with:

```python
        # Download with password in query string should succeed (served by nginx)
        response = self.client.get(f'{download_url}?password=testpassword')
        self.assertEqual(response.status_code, 200)
        self.assertIn('X-Accel-Redirect', response)
```

In `test_password_token`, after `upload = Upload.objects.create(...)` add `self.write_file('test.txt')`, and change its final assertion from `302` to `200`.

- [ ] **Step 6: Run the whole suite without AWS variables**

Run: `docker compose run --rm --build app python manage.py test store`
Expected: all tests OK. Then `grep -rn "boto3\|s3\b\|presigned\|UPLOAD_BUCKET" store shortterm_store requirements.txt` prints nothing.

- [ ] **Step 7: Commit**

```bash
git add -A store shortterm_store/settings.py requirements.txt
git commit -m "Serve downloads through nginx X-Accel-Redirect and drop S3"
```

---

### Task 5: Management commands and cron service

**Files:**
- Create: `store/management/__init__.py`, `store/management/commands/__init__.py`, `store/management/commands/cleanup_empty_stores.py`, `store/management/commands/verify_storage.py`, `store/test_commands.py`
- Modify: `docker-compose.yml`
- Delete: `store/jobs.py`

**Interfaces:**
- Consumes: `Item.objects.cleanup_empty()`, `storage.path_for_key`, `settings.STORAGE_ROOT`, `TempStorageMixin`.
- Produces: `manage.py cleanup_empty_stores`; `manage.py verify_storage` (prints a report, raises `CommandError` when any upload row has no file).

- [ ] **Step 1: Write the failing tests in `store/test_commands.py`**

```python
import uuid
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from .models import Item, Upload
from .testing import TempStorageMixin


class CleanupEmptyStoresTest(TestCase):
    def test_deletes_only_empty_items(self):
        empty = Item.objects.create()
        with_text = Item.objects.create(text='hi')
        with_upload = Item.objects.create()
        Upload.objects.create(item=with_upload, key=f'{with_upload.guid}/a.txt', url='x')

        call_command('cleanup_empty_stores')

        self.assertFalse(Item.objects.filter(pk=empty.pk).exists())
        self.assertTrue(Item.objects.filter(pk=with_text.pk).exists())
        self.assertTrue(Item.objects.filter(pk=with_upload.pk).exists())


class VerifyStorageTest(TempStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.item = Item.objects.create(text='x')

    def add_file(self, key, content=b'abc'):
        path = self.storage_root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def run_command(self):
        out = StringIO()
        call_command('verify_storage', stdout=out)
        return out.getvalue()

    def test_all_good(self):
        key = f'{self.item.guid}/a.txt'
        self.add_file(key, b'12345')
        Upload.objects.create(item=self.item, key=key, url='x')
        (self.storage_root / '.tmp' / 'partial').write_bytes(b'ignored')

        output = self.run_command()

        self.assertIn('Files on disk: 1 (5 bytes)', output)
        self.assertIn('Upload rows: 1', output)
        self.assertIn('Missing files: 0', output)
        self.assertIn('Orphan files: 0', output)

    def test_reports_orphans(self):
        self.add_file(f'{uuid.uuid4()}/orphan.txt')
        output = self.run_command()
        self.assertIn('Orphan files: 1', output)
        self.assertIn('orphan.txt', output)

    def test_missing_files_fail(self):
        Upload.objects.create(item=self.item, key=f'{self.item.guid}/gone.txt', url='x')
        out = StringIO()
        with self.assertRaisesMessage(CommandError, '1 upload(s) have no file'):
            call_command('verify_storage', stdout=out)
        self.assertIn(f'{self.item.guid}/gone.txt', out.getvalue())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm --build app python manage.py test store.test_commands`
Expected: `CommandError: Unknown command: 'cleanup_empty_stores'` / `'verify_storage'`.

- [ ] **Step 3: Implement the commands**

Create empty `store/management/__init__.py` and `store/management/commands/__init__.py`.

`store/management/commands/cleanup_empty_stores.py`:

```python
from django.core.management.base import BaseCommand

from store.models import Item


class Command(BaseCommand):
    help = 'Delete stores without name, text or uploads.'

    def handle(self, *args, **options):
        Item.objects.cleanup_empty()
```

`store/management/commands/verify_storage.py`:

```python
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from store import storage
from store.models import Upload


class Command(BaseCommand):
    help = 'Compare upload rows in the database with the files under STORAGE_ROOT.'

    def handle(self, *args, **options):
        root = Path(settings.STORAGE_ROOT).resolve()
        files = {}
        for path in root.rglob('*'):
            relative = path.relative_to(root)
            if path.is_file() and relative.parts[0] != '.tmp':
                files[relative.as_posix()] = path.stat().st_size

        keys = set(Upload.objects.values_list('key', flat=True))
        missing = sorted(key for key in keys if not self._exists(key))
        orphans = sorted(set(files) - keys)

        self.stdout.write(f'Files on disk: {len(files)} ({sum(files.values())} bytes)')
        self.stdout.write(f'Upload rows: {len(keys)}')
        self.stdout.write(f'Missing files: {len(missing)}')
        for key in missing:
            self.stdout.write(f'  {key}')
        self.stdout.write(f'Orphan files: {len(orphans)}')
        for key in orphans:
            self.stdout.write(f'  {key}')

        if missing:
            raise CommandError(f'{len(missing)} upload(s) have no file')

    @staticmethod
    def _exists(key):
        try:
            return storage.path_for_key(key).is_file()
        except storage.InvalidKey:
            return False
```

Delete `store/jobs.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm --build app python manage.py test store`
Expected: all tests OK.

- [ ] **Step 5: Add the cron service to `docker-compose.yml`**

Append under `services:` (after `db`):

```yaml
  cron:
    build: .
    restart: unless-stopped
    command: ["sh", "-c", "while true; do python manage.py cleanup_empty_stores; sleep 86400; done"]
    environment:
      SECRET_KEY: ${SECRET_KEY:-dev-insecure-key}
      DATABASE_URL: postgres://shorttermstore:${POSTGRES_PASSWORD:-shorttermstore}@db:5432/shorttermstore
      STORAGE_ROOT: /storage
    depends_on:
      db:
        condition: service_healthy
```

Verify: `docker compose up -d --build && sleep 5 && docker compose logs cron && docker compose ps && docker compose down`
Expected: `cron` is `Up`, no traceback in its logs.

- [ ] **Step 6: Commit**

```bash
git add -A store docker-compose.yml
git commit -m "Add cleanup and verify_storage commands, run cleanup from a cron service"
```

---

### Task 6: S3 file migration scripts

**Files:**
- Create: `deploy/s3_manifest.py`, `deploy/fetch_s3_manifest.py`, `deploy/test_fetch_s3_manifest.py`

**Interfaces:**
- Produces: manifest format, one JSON object per line: `{"key": str, "size": int, "last_modified": float (unix ts), "url": str}`. `fetch_s3_manifest.py STORAGE_DIR [--dry-run] < manifest`; exit code 1 when any download failed. Functions `target_path(root: Path, key: str) -> Path`, `is_current(path: Path, size: int, last_modified: float) -> bool`, `download(url: str, path: Path, last_modified: float) -> None`, `main(argv=None, stdin=None, stdout=None) -> int`.

- [ ] **Step 1: Write the failing tests in `deploy/test_fetch_s3_manifest.py`**

Uses `file://` URLs so no network is needed.

```python
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import fetch_s3_manifest as fetch


class FetchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.root = self.base / 'storage'
        self.root.mkdir()

    def entry(self, key, content, last_modified=1_700_000_000.0):
        src = self.source / key.replace('/', '_')
        src.write_bytes(content)
        return {'key': key, 'size': len(content), 'last_modified': last_modified, 'url': src.as_uri()}

    def run_main(self, entries, *args):
        stdin = io.StringIO(''.join(json.dumps(e) + '\n' for e in entries))
        stdout = io.StringIO()
        code = fetch.main([str(self.root), *args], stdin=stdin, stdout=stdout)
        return code, stdout.getvalue()

    def test_downloads_new_files(self):
        code, out = self.run_main([self.entry('abc/a b.txt', b'hello')])
        path = self.root / 'abc' / 'a b.txt'
        self.assertEqual(code, 0)
        self.assertEqual(path.read_bytes(), b'hello')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(path.stat().st_mtime, 1_700_000_000.0)
        self.assertIn('fetched=1 skipped=0 failed=0', out)

    def test_second_run_skips_current_files(self):
        entries = [self.entry('abc/a.txt', b'hello')]
        self.run_main(entries)
        code, out = self.run_main(entries)
        self.assertIn('fetched=0 skipped=1 failed=0', out)

    def test_refetches_changed_objects(self):
        self.run_main([self.entry('abc/a.txt', b'hello')])
        code, out = self.run_main([self.entry('abc/a.txt', b'hello!!', last_modified=1_800_000_000.0)])
        self.assertEqual((self.root / 'abc' / 'a.txt').read_bytes(), b'hello!!')
        self.assertIn('fetched=1', out)

    def test_dry_run_writes_nothing(self):
        code, out = self.run_main([self.entry('abc/a.txt', b'hello')], '--dry-run')
        self.assertFalse((self.root / 'abc').exists())
        self.assertIn('would fetch abc/a.txt', out)

    def test_unsafe_key_and_broken_url_fail(self):
        bad = {'key': '../escape.txt', 'size': 1, 'last_modified': 0, 'url': 'file:///nonexistent'}
        broken = {'key': 'abc/b.txt', 'size': 1, 'last_modified': 0, 'url': (self.base / 'nope').as_uri()}
        code, out = self.run_main([bad, broken, self.entry('abc/ok.txt', b'ok')])
        self.assertEqual(code, 1)
        self.assertFalse((self.base / 'escape.txt').exists())
        self.assertFalse((self.root / 'abc' / 'b.txt.part').exists())
        self.assertTrue((self.root / 'abc' / 'ok.txt').exists())
        self.assertIn('fetched=1 skipped=0 failed=2', out)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s deploy -p 'test_*.py'`
Expected: `ModuleNotFoundError: No module named 'fetch_s3_manifest'`.

- [ ] **Step 3: Implement `deploy/fetch_s3_manifest.py`**

Stdlib only; the host runs Python 3.10.

```python
"""Download the files listed in an s3_manifest.py manifest into a storage directory.

Usage: python3 fetch_s3_manifest.py STORAGE_DIR [--dry-run] < manifest.jsonl

A file is skipped when it already exists with the same size and an mtime at or after the
object's LastModified, so running it again only fetches what changed.
"""
import argparse
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path


def target_path(root, key):
    root = root.resolve()
    path = (root / key).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f'unsafe key: {key!r}')
    return path


def is_current(path, size, last_modified):
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    return st.st_size == size and st.st_mtime >= last_modified


def download(url, path, last_modified):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + '.part')
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(part, 'wb') as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        os.chmod(part, 0o644)
        os.utime(part, (last_modified, last_modified))
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def main(argv=None, stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('storage_dir', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    fetched = skipped = failed = fetched_bytes = 0
    for line in stdin:
        if not line.strip():
            continue
        entry = json.loads(line)
        key = entry['key']
        try:
            path = target_path(args.storage_dir, key)
            if is_current(path, entry['size'], entry['last_modified']):
                skipped += 1
                continue
            if args.dry_run:
                print(f'would fetch {key} ({entry["size"]} bytes)', file=stdout)
            else:
                download(entry['url'], path, entry['last_modified'])
            fetched += 1
            fetched_bytes += entry['size']
        except Exception as e:  # keep going; report at the end
            failed += 1
            print(f'FAILED {key}: {e}', file=stdout)

    print(f'fetched={fetched} skipped={skipped} failed={failed} bytes={fetched_bytes}', file=stdout)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s deploy -p 'test_*.py'`
Expected: `OK`.

- [ ] **Step 5: Create `deploy/s3_manifest.py`**

Runs on Jef's laptop in the `sts-3.12` virtualenv, which still has boto3 (`pip install boto3` if not).

```python
"""Print a JSON-lines manifest of the S3 upload bucket with presigned GET URLs.

Runs where AWS credentials are available. Pipe the output into fetch_s3_manifest.py on the
server, so the server never needs AWS credentials.
"""
import argparse
import json

import boto3
from botocore.config import Config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', default='geskens-shorttermstore-data')
    parser.add_argument('--profile', default='geskens')
    parser.add_argument('--region', default='eu-central-1')
    parser.add_argument('--expires', type=int, default=6 * 3600, help='URL lifetime in seconds')
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3 = session.client('s3', config=Config(signature_version='s3v4'))
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=args.bucket):
        for obj in page.get('Contents', []):
            url = s3.generate_presigned_url(
                'get_object', Params={'Bucket': args.bucket, 'Key': obj['Key']}, ExpiresIn=args.expires
            )
            print(json.dumps({
                'key': obj['Key'],
                'size': obj['Size'],
                'last_modified': obj['LastModified'].timestamp(),
                'url': url,
            }))


if __name__ == '__main__':
    main()
```

Check it (read-only, prints no URLs):
Run: `python deploy/s3_manifest.py | python3 -c "import sys,json; e=[json.loads(l) for l in sys.stdin]; print(len(e), round(sum(x['size'] for x in e)/2**30, 2), 'GiB')"`
Expected: `825 19.74 GiB` (or slightly more if people uploaded since 2026-10-13).

- [ ] **Step 6: Commit**

```bash
git add deploy/s3_manifest.py deploy/fetch_s3_manifest.py deploy/test_fetch_s3_manifest.py
git commit -m "Add scripts to copy the S3 bucket to the server via presigned URLs"
```

---

### Task 7: Deploy script, nginx vhost, env example

**Files:**
- Create: `deploy.sh`, `deploy/nginx.conf`
- Modify: `dot_env_example.env`

**Interfaces:**
- Produces: `./deploy.sh` (env `DEPLOY_HOST`, default `root@node3.geskens.be`; `DEPLOY_DIR`, default `/root/shortterm_store`).

- [ ] **Step 1: Create `deploy.sh`** and `chmod +x deploy.sh`

```bash
#!/usr/bin/env bash
# Sync the code to the server and rebuild/restart the containers.
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-root@node3.geskens.be}"
DEPLOY_DIR="${DEPLOY_DIR:-/root/shortterm_store}"

cd "$(dirname "$0")"

rsync -az --delete \
    --exclude /.git/ \
    --exclude /.env \
    --exclude /zappa_settings.json \
    --exclude /venv/ \
    --exclude /.idea/ \
    --exclude /.ropeproject/ \
    --exclude /.python-version \
    --exclude /storage/ \
    --exclude /staticfiles/ \
    --exclude __pycache__/ \
    --exclude '*.pyc' \
    --exclude .DS_Store \
    ./ "$DEPLOY_HOST:$DEPLOY_DIR/"

ssh "$DEPLOY_HOST" "cd '$DEPLOY_DIR' && docker compose up -d --build --remove-orphans && docker image prune -f"
```

Excluded paths are also protected from `--delete`, so the server's `.env` survives.

Verify without touching the server: `rsync -azn --delete --itemize-changes <same excludes> ./ /tmp/sts-dryrun/ | grep -E '\.env$|zappa|\.git/'` prints nothing. (Run the rsync line from the script with `-n` and a local target.)

- [ ] **Step 2: Create `deploy/nginx.conf`**

```nginx
# /etc/nginx/sites-enabled/shortterm.store
# At cutover: add shortterm.store to both server_name lines and switch the certificate paths
# to /etc/letsencrypt/live/shortterm.store/.

server {
    listen 80;
    listen [::]:80;
    server_name new.shortterm.store;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name new.shortterm.store;

    ssl_certificate /etc/letsencrypt/live/new.shortterm.store/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/new.shortterm.store/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 5G;
    # The default body temp dir is on the small root disk.
    client_body_temp_path /mnt/volume-ams3-01/shortterm_store/nginx_body;

    # Files authorized by Django via X-Accel-Redirect.
    location /_protected/ {
        internal;
        alias /mnt/volume-ams3-01/shortterm_store/storage/;
    }

    location / {
        proxy_pass http://127.0.0.1:8400;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

- [ ] **Step 3: Replace `dot_env_example.env`**

```
# Server: /root/shortterm_store/.env (read by docker compose)
SECRET_KEY=keep-the-current-production-key
DEBUG=False
ALLOWED_HOSTS=new.shortterm.store
CSRF_TRUSTED_ORIGINS=https://new.shortterm.store
POSTGRES_PASSWORD=generate-with-openssl-rand-hex-24
STORAGE_DIR=/mnt/volume-ams3-01/shortterm_store/storage
```

- [ ] **Step 4: Commit**

```bash
git add deploy.sh deploy/nginx.conf dot_env_example.env
git commit -m "Add deploy script and nginx vhost for node3"
```

---

### Task 8: Provision node3 and first deploy (ops, confirm with Jef before each step that changes the server)

**Files:** none in the repo.

- [ ] **Step 1: Create directories**

```bash
ssh root@node3.geskens.be 'set -e
mkdir -p /root/shortterm_store
B=/mnt/volume-ams3-01/shortterm_store
mkdir -p $B/storage/.tmp $B/nginx_body
chown -R 33:33 $B/storage $B/nginx_body
chmod 755 $B $B/storage'
```

- [ ] **Step 2: Write the server `.env` without printing secrets**

The `SECRET_KEY` comes from the local `zappa_settings.json`; the Postgres password is generated on the server.

```bash
python3 -c "import json; print(json.load(open('zappa_settings.json'))['dev']['environment_variables']['SECRET_KEY'])" \
| ssh root@node3.geskens.be 'set -e
read -r SECRET_KEY
umask 077
cat > /root/shortterm_store/.env <<EOF
SECRET_KEY=$SECRET_KEY
DEBUG=False
ALLOWED_HOSTS=new.shortterm.store
CSRF_TRUSTED_ORIGINS=https://new.shortterm.store
POSTGRES_PASSWORD=$(openssl rand -hex 24)
STORAGE_DIR=/mnt/volume-ams3-01/shortterm_store/storage
EOF
sed "s/=.*/=…/" /root/shortterm_store/.env'
```

Expected: the six variable names printed with masked values.

Watch out: if the SECRET_KEY contains `$` or `` ` ``, the heredoc expands it. Check with `grep -c '[$`]'` on the local value first; if it does, write that line with `printf '%s\n' "SECRET_KEY=$SECRET_KEY"` instead.

- [ ] **Step 3: First deploy**

Run: `./deploy.sh`
Then: `ssh root@node3.geskens.be 'cd /root/shortterm_store && docker compose ps && curl -s -o /dev/null -w "%{http_code}\n" -H "Host: new.shortterm.store" http://127.0.0.1:8400/'`
Expected: `app`, `db`, `cron` running; `302`.

- [ ] **Step 4: Certificate for new.shortterm.store**

Install an HTTP-only vhost first (the full one references a certificate that does not exist yet):

```bash
ssh root@node3.geskens.be 'set -e
cat > /etc/nginx/sites-enabled/shortterm.store <<'"'"'EOF'"'"'
server {
    listen 80;
    listen [::]:80;
    server_name new.shortterm.store;
    location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 404; }
}
EOF
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/certbot -d new.shortterm.store --non-interactive'
```

Expected: certbot reports the certificate saved under `/etc/letsencrypt/live/new.shortterm.store/`.

- [ ] **Step 5: Install the real vhost**

```bash
scp deploy/nginx.conf root@node3.geskens.be:/etc/nginx/sites-enabled/shortterm.store
ssh root@node3.geskens.be 'nginx -t && systemctl reload nginx'
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://new.shortterm.store/
curl -s -o /dev/null -w '%{http_code}\n' https://new.shortterm.store/static/admin/css/base.css
```

Expected: `302 https://new.shortterm.store/<uuid>/` and `200`.

After the smoke test below, `curl -sI` a downloaded `.png` and an uploaded `.html` file's download URL: the png shows `Content-Type: image/png`, the html shows `application/octet-stream` + `Content-Disposition: attachment`, and both carry `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`.

- [ ] **Step 6: Smoke test with an empty database**

In a browser on `https://new.shortterm.store/`: create a store, upload a small file via the uploader, upload a >10 MB file, paste an image into the text, download both, delete one, set a password and open the store again in a private window.
Then on the server: `ls -ln /mnt/volume-ams3-01/shortterm_store/storage/*/` shows files owned by `33 33` with mode `-rw-r--r--`.

---

### Task 9: Copy data from AWS and verify (ops, confirm with Jef first)

- [ ] **Step 1: Copy files**

From the repo root on the laptop:

```bash
python deploy/s3_manifest.py | ssh root@node3.geskens.be 'python3 /root/shortterm_store/deploy/fetch_s3_manifest.py /mnt/volume-ams3-01/shortterm_store/storage && chown -R 33:33 /mnt/volume-ams3-01/shortterm_store/storage'
```

Expected: `fetched=825 skipped=0 failed=0 bytes=21192342454` (give or take recent uploads). If `failed>0`, rerun the same command; current files are skipped.

- [ ] **Step 2: Copy the database**

The RDS password is read from the local `zappa_settings.json` and sent over stdin, so it never appears in a command line or output.

```bash
python3 -c "
import json, urllib.parse
url = json.load(open('zappa_settings.json'))['dev']['environment_variables']['DATABASE_URL']
print(urllib.parse.urlparse(url).password)" \
| ssh root@node3.geskens.be 'set -e
read -r PGPASSWORD; export PGPASSWORD
cd /root/shortterm_store
umask 077
docker run --rm -e PGPASSWORD postgres:16 pg_dump \
  -h oemfoe-aws-2.cowc6c0hrwed.eu-central-1.rds.amazonaws.com -U postgres -d shorttermstore \
  --no-owner --no-acl -Fc > shorttermstore.dump
docker compose stop app cron
set +e
docker compose exec -T db pg_restore --clean --if-exists --no-owner --role=shorttermstore \
  -U shorttermstore -d shorttermstore < shorttermstore.dump
rc=$?
docker compose start app cron
rm -f shorttermstore.dump
exit $rc'
```

Expected: the app is running again whatever happens. `pg_restore` may exit 1 with only notices about objects that did not exist; any other error means restore again before continuing. The dump (all user data) is created mode 0600 and deleted right after the restore.

- [ ] **Step 3: Compare row counts**

```bash
ssh root@node3.geskens.be 'cd /root/shortterm_store && docker compose exec -T db psql -U shorttermstore -d shorttermstore -c "select (select count(*) from store_item) items, (select count(*) from store_upload) uploads;"'
```

Compare with the same query against RDS (same `pg_dump` connection, using `psql` instead). Numbers must match.

- [ ] **Step 4: Verify storage against the database**

```bash
ssh root@node3.geskens.be 'cd /root/shortterm_store && docker compose exec -T app python manage.py verify_storage'
```

Expected: `Missing files: 0`. Orphan files are listed for information (files in S3 with no upload row); keep them.

- [ ] **Step 5: Check real data in the browser**

On `https://new.shortterm.store/<existing guid>/` for a few real stores (pick guids from the restored DB): images and carousel load, an mp3 plays and can be scrubbed (Range requests), a password-protected store with embedded images shows them after unlocking, and a large file (the 3.42 GiB one) starts downloading.

---

### Task 10: Cutover (ops, Jef picks the moment)

- [ ] **Step 1:** A day before, lower the TTL of the `shortterm.store` record in Route 53 to 60 s.
- [ ] **Step 2: Prepare node3 for the name, before DNS moves.** Add `shortterm.store` to the server `.env` (`ALLOWED_HOSTS=shortterm.store,new.shortterm.store`, `CSRF_TRUSTED_ORIGINS=https://shortterm.store,https://new.shortterm.store`) and run `docker compose up -d` in `/root/shortterm_store`. In `/etc/nginx/sites-enabled/shortterm.store` add `shortterm.store` to **both** `server_name` lines (the 443 block keeps the `new.shortterm.store` certificate for now, so the brief gap shows a name mismatch for this site instead of another project's site, and the ACME challenge for `shortterm.store` hits our port-80 block). `nginx -t && systemctl reload nginx`.
- [ ] **Step 3: Stop writes on the old site.** `aws --profile geskens --region eu-central-1 lambda put-function-concurrency --function-name shorttermstore-dev --reserved-concurrent-executions 0` (and the same for `shorttermstore-test`). The old site now errors instead of accepting uploads. Undo with `aws ... lambda delete-function-concurrency --function-name shorttermstore-dev`.
- [ ] **Step 4: Final sync.** Rerun Task 9 steps 1–4 (delta copy of files, fresh dump/restore, counts, `verify_storage`).
- [ ] **Step 5: Switch DNS.** Point `shortterm.store` to `188.166.33.64` in Route 53.
- [ ] **Step 6: Certificate.** As soon as `dig +short shortterm.store @1.1.1.1` returns `188.166.33.64`: `certbot certonly --webroot -w /var/www/certbot -d shortterm.store -d new.shortterm.store --cert-name shortterm.store`. Then set the certificate paths in the 443 block to `/etc/letsencrypt/live/shortterm.store/` and `nginx -t && systemctl reload nginx`. Expected TLS gap: roughly the TTL plus a minute.
- [ ] **Step 7:** Check `https://shortterm.store/` and an existing store with uploads. Update `deploy/nginx.conf` in the repo to match the server (both names, new certificate paths) and commit it.

### Task 11: Decommission AWS (after about a week; each step needs Jef's explicit go)

- [ ] `zappa undeploy dev` and `zappa undeploy test`.
- [ ] Delete the `shorttermstore` database on RDS `oemfoe-aws-2` (the instance may host other databases; do not delete the instance without checking).
- [ ] Delete buckets `geskens-shorttermstore-data` and `geskens-shorttermstore-app`.
- [ ] Delete the two ACM certificates for `shortterm.store` and `test.shortterm.store` (us-east-1) and the SES SMTP IAM user.
- [ ] Move the `shortterm.store` DNS zone off Route 53 (Jef, outside this plan).
- [ ] Remove `new.shortterm.store` from DNS, nginx, `.env`, and the certificate; delete `zappa_settings.json` locally.
