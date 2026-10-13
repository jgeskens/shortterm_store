import json
import uuid
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.hashers import make_password
from .models import Item
from .testing import TempStorageMixin

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

    def test_set_password(self):
        # Initial check
        response = self.client.get(f'/{self.item_guid}/?json')
        data = json.loads(response.content)
        self.assertFalse(data['has_password'])

        # Set password
        response = self.client.post(
            f'/{self.item_guid}/',
            data=json.dumps({'password': 'testpassword'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

        # Check if has_password is now true
        response = self.client.get(f'/{self.item_guid}/?json')
        data = json.loads(response.content)
        self.assertTrue(data['has_password'])

        # Verify password correctly
        response = self.client.post(
            f'/{self.item_guid}/verify-password/',
            data=json.dumps({'password': 'testpassword'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)['correct'])

        # Verify password incorrectly
        response = self.client.post(
            f'/{self.item_guid}/verify-password/',
            data=json.dumps({'password': 'wrongpassword'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(json.loads(response.content)['correct'])

    def test_protected_endpoints(self):
        # Set password
        self.item.password = make_password('testpassword')
        self.item.save()

        # Create an upload
        from .models import Upload
        upload = Upload.objects.create(item=self.item, key=f'{self.item_guid}/test.txt', url='http://example.com/test.txt')
        self.write_file('test.txt')

        # GET detail without password should fail
        response = self.client.get(f'/{self.item_guid}/?json')
        self.assertEqual(response.status_code, 403)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'locked')

        # GET detail with correct password in header should succeed
        response = self.client.get(f'/{self.item_guid}/?json', **{'HTTP_X_ITEM_PASSWORD': 'testpassword'})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('text', data)

        # Download without password should fail
        download_url = reverse('upload_download', args=(self.item_guid, str(upload.guid)))
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 403)

        # Download with password in query string should succeed (served by nginx)
        response = self.client.get(f'{download_url}?password=testpassword')
        self.assertEqual(response.status_code, 200)
        self.assertIn('X-Accel-Redirect', response)

        # POST update without password should fail
        response = self.client.post(
            f'/{self.item_guid}/',
            data=json.dumps({'text': 'new text'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)

        # POST update with password in header should succeed
        response = self.client.post(
            f'/{self.item_guid}/',
            data=json.dumps({'text': 'new text'}),
            content_type='application/json',
            **{'HTTP_X_ITEM_PASSWORD': 'testpassword'}
        )
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.text, 'new text')

    def test_upload_endpoints_password(self):
        # Set password
        self.item.password = make_password('testpassword')
        self.item.save()

        # generate_upload_params without password should fail
        response = self.client.post(
            f'/{self.item_guid}/generate-upload-params/',
            data={'filename': 'test.png'}
        )
        self.assertEqual(response.status_code, 403)

        # generate_upload_params with password should succeed
        response = self.client.post(
            f'/{self.item_guid}/generate-upload-params/',
            data={'filename': 'test.png'},
            **{'HTTP_X_ITEM_PASSWORD': 'testpassword'}
        )
        self.assertEqual(response.status_code, 200)

        # upload without password should fail
        response = self.client.post(
            f'/{self.item_guid}/upload/',
            data=json.dumps({'key': f'{self.item_guid}/test.png', 'url': 'http://example.com/test.png'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)

        # upload with password should succeed
        response = self.client.post(
            f'/{self.item_guid}/upload/',
            data=json.dumps({'key': f'{self.item_guid}/test.png', 'url': 'http://example.com/test.png'}),
            content_type='application/json',
            **{'HTTP_X_ITEM_PASSWORD': 'testpassword'}
        )
        self.assertEqual(response.status_code, 200)

    def test_password_token(self):
        # Set password
        password = 'testpassword'
        self.item.password = make_password(password)
        self.item.save()

        # Get detail with correct password to get token
        response = self.client.get(f'/{self.item_guid}/?json', **{'HTTP_X_ITEM_PASSWORD': password})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        password_token = data.get('t')
        self.assertTrue(password_token)

        # Access with token should succeed
        response = self.client.get(f'/{self.item_guid}/?json&t={password_token}')
        self.assertEqual(response.status_code, 200)

        # Access with wrong token should fail
        response = self.client.get(f'/{self.item_guid}/?json&t=wrongtoken')
        self.assertEqual(response.status_code, 403)

        # Create an upload
        from .models import Upload
        upload = Upload.objects.create(item=self.item, key=f'{self.item_guid}/test.txt', url='http://example.com/test.txt')
        self.write_file('test.txt')

        # Download with token should succeed
        download_url = reverse('upload_download', args=(self.item_guid, str(upload.guid)))
        response = self.client.get(f'{download_url}?t={password_token}')
        self.assertEqual(response.status_code, 200)


class ShortLinkExpiryTest(TestCase):
    def test_json_includes_expiry_timestamp_while_short_link_is_valid(self):
        item = Item.objects.create(text='hello')
        data = json.loads(self.client.get(f'/{item.guid}/?json').content)
        self.assertEqual(data['short_link_expires_at'], item.shortcut_expires().isoformat())

    def test_json_expiry_timestamp_is_null_after_short_link_expired(self):
        from datetime import timedelta
        from django.utils.timezone import now
        item = Item.objects.create(text='hello')
        Item.objects.filter(pk=item.pk).update(created=now() - timedelta(minutes=10))
        data = json.loads(self.client.get(f'/{item.guid}/?json').content)
        self.assertIsNone(data['short_link_expires_at'])


class DesignByHostTest(TestCase):
    def render_with_host(self, host):
        # Uses the configured NEW_DESIGN_HOSTS, so these cover its default.
        item = Item.objects.create(text='hello')
        with self.settings(ALLOWED_HOSTS=['*']):
            return self.client.get(f'/{item.guid}/', HTTP_HOST=host)

    def test_new_domain_gets_new_design(self):
        response = self.render_with_host('new.shortterm.store')
        self.assertTemplateUsed(response, 'store/item_detail.html')
        self.assertTemplateNotUsed(response, 'store/item_detail_classic.html')

    def test_original_domain_gets_new_design(self):
        response = self.render_with_host('shortterm.store')
        self.assertTemplateUsed(response, 'store/item_detail.html')
        self.assertTemplateNotUsed(response, 'store/item_detail_classic.html')

    def test_unknown_host_gets_classic_design(self):
        response = self.render_with_host('localhost:8000')
        self.assertTemplateUsed(response, 'store/item_detail_classic.html')
