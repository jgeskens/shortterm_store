import json
import uuid
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.hashers import make_password
from .models import Item

class PasswordProtectionTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.item_guid = str(uuid.uuid4())
        self.item = Item.objects.create(guid=self.item_guid)

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

        # Download with password in query string should succeed (redirect to S3)
        response = self.client.get(f'{download_url}?password=testpassword')
        self.assertEqual(response.status_code, 302)

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
