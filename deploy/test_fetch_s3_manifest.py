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
