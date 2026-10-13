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
MAX_KEY_LENGTH = 255  # Upload.key max_length
MAX_FILENAME_BYTES = 255  # filesystem limit

# Types that are safe to show inline on our own origin. Anything else (HTML, SVG, XML, JS, ...)
# could run script, so it is served as an attachment.
# PDFs are not inline: browsers refuse to render them under nginx's CSP sandbox.
INLINE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'text/plain'}


class InvalidKey(ValueError):
    pass


def clean_filename(filename):
    name = os.path.basename((filename or '').replace('\\', '/')).strip()
    if name in ('', '.', '..'):
        raise InvalidKey(f'Invalid filename: {filename!r}')
    return name


def key_for(item_guid, filename):
    name = clean_filename(filename)
    key = f'{item_guid}/{name}'
    if len(key) > MAX_KEY_LENGTH or len(name.encode('utf-8')) > MAX_FILENAME_BYTES:
        raise InvalidKey(f'Filename too long: {filename!r}')
    return key


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


def is_inline_safe(ctype):
    return ctype in INLINE_TYPES or ctype.startswith(('audio/', 'video/'))
