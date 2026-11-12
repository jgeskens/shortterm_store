import errno
import json
import os
from unittest import mock
import uuid

from django.contrib.auth.hashers import make_password
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from . import views
from .models import DROPBOX, Item, Upload
from .testing import TempStorageMixin


@override_settings(NEW_DESIGN_HOSTS=['testserver'])
class DropboxUploadTest(TempStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.guid = str(uuid.uuid4())
        self.item = Item.objects.create(guid=self.guid, password=make_password('pw'), visitor_access=DROPBOX)

    def get_params(self, filename, **extra):
        return self.client.post(f'/{self.guid}/generate-upload-params/', {'filename': filename}, **extra)

    def send_file(self, params, content=b'data'):
        data = dict(params['fields'])
        data['file'] = SimpleUploadedFile('file.bin', content)
        return self.client.post(params['url'], data)

    def register(self, key, **extra):
        return self.client.post(
            f'/{self.guid}/upload/',
            data=json.dumps({'key': key, 'url': 'x'}),
            content_type='application/json',
            **extra,
        )

    def put_file(self, name, content=b'orig', row=False):
        path = self.storage_root / self.guid / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if row:
            Upload.objects.create(item=self.item, key=f'{self.guid}/{name}', url='x')

    def visitor_upload(self, name, content=b'new'):
        response = self.get_params(name)
        self.assertEqual(response.status_code, 200)
        params = response.json()
        self.assertEqual(self.send_file(params, content).status_code, 204)
        self.assertEqual(self.register(params['fields']['key']).status_code, 200)
        return params['fields']['key']

    def test_dropbox_visitor_full_flow(self):
        key = self.visitor_upload('a.txt')
        self.assertEqual(key, f'{self.guid}/a.txt')
        self.assertTrue(Upload.objects.filter(item=self.item, key=key).exists())

    def test_dropbox_visitor_gets_numbered_copy(self):
        self.put_file('IMG_0042.jpg', row=True)
        key = self.visitor_upload('IMG_0042.jpg')
        self.assertEqual(key, f'{self.guid}/IMG_0042 (2).jpg')
        self.assertEqual((self.storage_root / self.guid / 'IMG_0042.jpg').read_bytes(), b'orig')

    def test_dropbox_numbering_counts_up(self):
        self.put_file('a.txt', row=True)
        self.put_file('a (2).txt', row=True)
        self.assertEqual(self.visitor_upload('a.txt'), f'{self.guid}/a (3).txt')

    def test_dropbox_no_extension(self):
        self.put_file('README', row=True)
        self.assertEqual(self.visitor_upload('README'), f'{self.guid}/README (2)')

    def test_dropbox_numbers_name_taken_on_disk_only(self):
        self.put_file('a.txt')
        self.assertEqual(self.visitor_upload('a.txt'), f'{self.guid}/a (2).txt')

    def test_dropbox_numbers_name_taken_in_db_only(self):
        Upload.objects.create(item=self.item, key=f'{self.guid}/a.txt', url='x')
        params = self.get_params('a.txt').json()
        self.assertEqual(params['fields']['key'], f'{self.guid}/a (2).txt')

    def test_dropbox_numbered_name_too_long(self):
        name = 'a' * 218
        self.assertEqual(len(f'{self.guid}/{name}'), 255)
        self.put_file(name, row=True)
        self.assertEqual(self.get_params(name).status_code, 400)

    def test_dropbox_receive_refuses_overwrite(self):
        params = self.get_params('a.txt').json()
        self.put_file('a.txt', b'other')
        response = self.send_file(params, b'mine')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'error': 'exists'})
        self.assertEqual((self.storage_root / self.guid / 'a.txt').read_bytes(), b'other')

    def test_dropbox_register_existing_row_conflict(self):
        self.put_file('a.txt', row=True)
        before = Upload.objects.get(item=self.item).modified
        self.assertEqual(self.register(f'{self.guid}/a.txt').status_code, 409)
        self.assertEqual(Upload.objects.get(item=self.item).modified, before)

    def test_dropbox_register_requires_file(self):
        self.assertEqual(self.register(f'{self.guid}/a.txt').status_code, 400)

    def test_read_visitor_cannot_upload(self):
        self.item.visitor_access = 'read'
        self.item.save()
        self.assertEqual(self.get_params('a.txt').status_code, 403)
        self.assertEqual(self.register(f'{self.guid}/a.txt').status_code, 403)

    def test_closed_visitor_cannot_upload(self):
        self.item.visitor_access = 'closed'
        self.item.save()
        self.assertEqual(self.get_params('a.txt').status_code, 403)
        self.assertEqual(self.register(f'{self.guid}/a.txt').status_code, 403)

    def test_classic_host_dropbox_visitor_cannot_upload(self):
        self.assertEqual(self.get_params('a.txt', HTTP_HOST='localhost').status_code, 403)

    def test_owner_still_replaces(self):
        for content in (b'one', b'two'):
            params = self.get_params('a.txt', HTTP_X_ITEM_PASSWORD='pw').json()
            self.assertEqual(params['fields']['key'], f'{self.guid}/a.txt')
            self.assertEqual(self.send_file(params, content).status_code, 204)
        self.assertEqual((self.storage_root / self.guid / 'a.txt').read_bytes(), b'two')

    def test_old_string_token_still_replaces(self):
        self.put_file('a.txt')
        key = f'{self.guid}/a.txt'
        params = {'url': f'/{self.guid}/upload/receive/',
                  'fields': {'key': key, 'token': signing.dumps(key, salt=views.UPLOAD_TOKEN_SALT)}}
        self.assertEqual(self.send_file(params, b'new').status_code, 204)
        self.assertEqual((self.storage_root / self.guid / 'a.txt').read_bytes(), b'new')



@override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=0)
class DropboxTempFileUploadTest(DropboxUploadTest):
    """Same flow, but uploads arrive as temporary files (the temporary_file_path branch)."""

    def folder_files(self):
        return sorted(p.name for p in (self.storage_root / self.guid).iterdir())

    def test_temp_file_upload_is_not_in_memory(self):
        params = self.get_params('big.bin').json()
        data = dict(params['fields'])
        data['file'] = SimpleUploadedFile('file.bin', b'x')
        with mock.patch('store.storage.os.link', wraps=os.link) as link:
            self.assertEqual(self.client.post(params['url'], data).status_code, 204)
        self.assertEqual(link.call_count, 1)
        self.assertEqual(self.folder_files(), ['big.bin'])

    def test_temp_file_conflict_leaves_no_temp_files(self):
        params = self.get_params('a.txt').json()
        self.put_file('a.txt', b'other')
        self.assertEqual(self.send_file(params, b'mine').status_code, 409)
        self.assertEqual((self.storage_root / self.guid / 'a.txt').read_bytes(), b'other')
        self.assertEqual(self.folder_files(), ['a.txt'])

    def test_cross_device_fallback_copies_then_links(self):
        params = self.get_params('a.txt').json()
        real_link = os.link
        calls = []

        def link(src, dst):
            calls.append(src)
            if len(calls) == 1:
                raise OSError(errno.EXDEV, 'cross-device')
            return real_link(src, dst)

        with mock.patch('store.storage.os.link', side_effect=link):
            self.assertEqual(self.send_file(params, b'mine').status_code, 204)
        self.assertEqual(len(calls), 2)
        self.assertEqual((self.storage_root / self.guid / 'a.txt').read_bytes(), b'mine')
        self.assertEqual(self.folder_files(), ['a.txt'])

    def test_cross_device_fallback_still_refuses_overwrite(self):
        params = self.get_params('a.txt').json()
        self.put_file('a.txt', b'other')
        real_link = os.link
        calls = []

        def link(src, dst):
            calls.append(src)
            if len(calls) == 1:
                raise OSError(errno.EXDEV, 'cross-device')
            return real_link(src, dst)

        with mock.patch('store.storage.os.link', side_effect=link):
            self.assertEqual(self.send_file(params, b'mine').status_code, 409)
        self.assertEqual((self.storage_root / self.guid / 'a.txt').read_bytes(), b'other')
        self.assertEqual(self.folder_files(), ['a.txt'])


@override_settings(NEW_DESIGN_HOSTS=['testserver'])
class VisitorMultipartCsrfTest(TempStorageMixin, TestCase):
    """The real app's CSRF middleware reads request.POST first, so request.body is unreadable."""

    def setUp(self):
        super().setUp()
        self.guid = str(uuid.uuid4())
        self.item = Item.objects.create(guid=self.guid, password=make_password('pw'), visitor_access=DROPBOX)
        self.client = self.client_class(enforce_csrf_checks=True)
        self.client.get(f'/{self.guid}/')
        self.csrf = self.client.cookies['csrftoken'].value

    def post_params(self, filename):
        return self.client.post(
            f'/{self.guid}/generate-upload-params/', {'filename': filename}, HTTP_X_CSRFTOKEN=self.csrf
        )

    def test_dropbox_visitor_params_with_csrf(self):
        path = self.storage_root / self.guid / 'a.txt'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'x')
        response = self.post_params('a.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fields']['key'], f'{self.guid}/a (2).txt')

    def test_read_visitor_params_with_csrf(self):
        self.item.visitor_access = 'read'
        self.item.save()
        self.assertEqual(self.post_params('a.txt').status_code, 403)
