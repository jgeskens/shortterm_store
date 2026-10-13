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
