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
