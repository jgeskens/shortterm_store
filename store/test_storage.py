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
