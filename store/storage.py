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


def save(key, uploaded_file, replace=True):
    """Write the file at key. With replace=False, raise FileExistsError rather than overwrite."""
    path = path_for_key(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(uploaded_file, 'temporary_file_path'):
        temp_name = uploaded_file.temporary_file_path()
    else:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            for chunk in uploaded_file.chunks():
                out.write(chunk)
        temp_name = out.name
    try:
        os.chmod(temp_name, FILE_MODE)
        if replace:
            if hasattr(uploaded_file, 'temporary_file_path'):
                shutil.move(temp_name, path)
            else:
                os.replace(temp_name, path)
        else:
            # os.link fails if the target exists, so two racing uploads can't both win.
            os.link(temp_name, path)
            os.unlink(temp_name)
    except BaseException:
        if not replace:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        raise
    return path


def _numbered(name, n):
    stem, ext = os.path.splitext(name)
    if not stem:  # dotfile such as ".env"
        stem, ext = name, ''
    return f'{stem} ({n}){ext}'


def free_key_for(item_guid, filename, taken):
    """Key for filename that is neither in taken nor on disk, numbering it if needed."""
    name = clean_filename(filename)
    n = 1
    while True:
        candidate = name if n == 1 else _numbered(name, n)
        key = key_for(item_guid, candidate)
        if key not in taken and not path_for_key(key).exists():
            return key
        n += 1


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
