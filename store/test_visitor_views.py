import json
import uuid

from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings

from .access import get_item_password_token
from .models import Item, Upload
from .testing import TempStorageMixin


@override_settings(NEW_DESIGN_HOSTS=['testserver'])
class VisitorViewsTest(TempStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.guid = str(uuid.uuid4())
        self.item = Item.objects.create(guid=self.guid, password=make_password('pw'))
        self.token = get_item_password_token(self.item)
        self.item.text = f'see http://x/{self.guid}/?t={self.token} and ?a=1&t={self.token}'
        self.item.save()
        key = f'{self.guid}/a.txt'
        path = self.storage_root / self.guid / 'a.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'hello')
        self.upload = Upload.objects.create(item=self.item, key=key, url='u')

    def set_access(self, value):
        self.item.visitor_access = value
        self.item.save()

    def get_json(self, **extra):
        return self.client.get(f'/{self.guid}/?json', **extra)

    def post(self, data, **extra):
        return self.client.post(
            f'/{self.guid}/', data=json.dumps(data), content_type='application/json', **extra
        )

    def download(self, **extra):
        return self.client.get(f'/{self.guid}/upload/{self.upload.guid}/download/', **extra)

    def test_read_visitor_json(self):
        self.set_access('read')
        response = self.get_json()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['viewer'], 'visitor')
        self.assertEqual(body['visitor_access'], 'read')
        self.assertEqual(body['t'], '')
        self.assertEqual(body['password_token'], '')

    def test_read_visitor_text_has_no_token(self):
        self.set_access('read')
        response = self.get_json()
        self.assertNotIn(self.token.encode(), response.content)

    def test_dropbox_visitor_json(self):
        self.set_access('dropbox')
        response = self.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['viewer'], 'visitor')
        self.assertEqual(response.json()['visitor_access'], 'dropbox')

    def test_closed_visitor_json_locked(self):
        response = self.get_json()
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body['has_password'], True)
        self.assertEqual(body['error'], 'locked')

    def test_wrong_password_header_gets_visitor_view(self):
        self.set_access('read')
        response = self.get_json(HTTP_X_ITEM_PASSWORD='old')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['viewer'], 'visitor')

    def test_owner_json_unchanged(self):
        self.set_access('read')
        response = self.get_json(HTTP_X_ITEM_PASSWORD='pw')
        body = response.json()
        self.assertEqual(body['viewer'], 'owner')
        self.assertEqual(body['visitor_access'], 'read')
        self.assertTrue(body['t'])
        self.assertTrue(body['password_token'])
        self.assertIn(self.token, body['text'])

    @override_settings(NEW_DESIGN_HOSTS=['testserver'], ALLOWED_HOSTS=['*'])
    def test_classic_host_visitor_locked(self):
        self.set_access('read')
        response = self.get_json(HTTP_HOST='localhost')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'locked')

    def test_visitor_cannot_post(self):
        for access in ('read', 'dropbox'):
            self.set_access(access)
            before = Item.objects.values().get(guid=self.guid)
            for data in ({'text': 'x'}, {'name': 'x'}, {'mode': 'reader'},
                         {'password': ''}, {'visitor_access': 'closed'}):
                with self.subTest(access=access, data=data):
                    self.assertEqual(self.post(data).status_code, 403)
                    self.assertEqual(Item.objects.values().get(guid=self.guid), before)

    def test_owner_sets_visitor_access_alone(self):
        old_hash = self.item.password
        response = self.post({'visitor_access': 'dropbox'}, HTTP_X_ITEM_PASSWORD='pw')
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.visitor_access, 'dropbox')
        self.assertEqual(self.item.password, old_hash)

    def test_owner_invalid_visitor_access(self):
        response = self.post({'visitor_access': 'public', 'text': 'changed'}, HTTP_X_ITEM_PASSWORD='pw')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'invalid visitor_access'})
        self.item.refresh_from_db()
        self.assertNotEqual(self.item.text, 'changed')
        self.assertEqual(self.item.visitor_access, 'closed')

    def test_visitor_download(self):
        for access, expected in (('read', 200), ('dropbox', 200), ('closed', 403)):
            self.set_access(access)
            with self.subTest(access=access):
                response = self.download()
                self.assertEqual(response.status_code, expected)
                if expected == 200:
                    self.assertTrue(response['X-Accel-Redirect'])

    def test_visitor_cannot_delete(self):
        for access in ('read', 'dropbox'):
            self.set_access(access)
            with self.subTest(access=access):
                response = self.client.post(f'/{self.guid}/upload/{self.upload.guid}/delete/')
                self.assertEqual(response.status_code, 403)
                self.assertTrue(Upload.objects.filter(pk=self.upload.pk).exists())
                self.assertTrue((self.storage_root / self.guid / 'a.txt').exists())

    def test_owner_delete_still_works(self):
        response = self.client.post(
            f'/{self.guid}/upload/{self.upload.guid}/delete/', HTTP_X_ITEM_PASSWORD='pw'
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Upload.objects.filter(pk=self.upload.pk).exists())
