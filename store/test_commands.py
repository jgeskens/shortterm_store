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
