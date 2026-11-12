from django.contrib.auth.hashers import make_password
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from store.access import DROPBOX, OWNER, READ, access_for, strip_token
from store.models import Item


@override_settings(NEW_DESIGN_HOSTS=['testserver'])
class AccessForTest(TestCase):
    def request(self, **extra):
        return RequestFactory().get('/', **extra)

    def item(self, visitor_access='closed', password='pw'):
        return Item.objects.create(
            password=make_password(password) if password else '',
            visitor_access=visitor_access,
        )

    def test_no_password_is_owner(self):
        self.assertEqual(access_for(self.item(password=''), self.request()), OWNER)

    def test_correct_password_is_owner(self):
        item = self.item('read')
        self.assertEqual(access_for(item, self.request(HTTP_X_ITEM_PASSWORD='pw')), OWNER)

    def test_closed_visitor_has_no_access(self):
        self.assertIsNone(access_for(self.item('closed'), self.request()))

    def test_read_visitor(self):
        self.assertEqual(access_for(self.item('read'), self.request()), READ)

    def test_dropbox_visitor(self):
        self.assertEqual(access_for(self.item('dropbox'), self.request()), DROPBOX)

    def test_wrong_password_falls_back_to_visitor(self):
        item = self.item('read')
        self.assertEqual(access_for(item, self.request(HTTP_X_ITEM_PASSWORD='nope')), READ)

    @override_settings(ALLOWED_HOSTS=['*'])
    def test_classic_host_visitor_has_no_access(self):
        item = self.item('read')
        self.assertIsNone(access_for(item, self.request(HTTP_HOST='localhost')))


class VisitorAccessDefaultTest(TestCase):
    def test_default_is_closed(self):
        self.assertEqual(Item.objects.create().visitor_access, 'closed')


class StripTokenTest(SimpleTestCase):
    T = 'a' * 64

    def test_strip_token_only_param(self):
        self.assertEqual(strip_token(f'![x](/g/upload/u/download/?t={self.T})', self.T), '![x](/g/upload/u/download/)')

    def test_strip_token_first_of_two(self):
        self.assertEqual(strip_token(f'/d/?t={self.T}&x=1', self.T), '/d/?x=1')

    def test_strip_token_last_of_two(self):
        self.assertEqual(strip_token(f'/d/?x=1&t={self.T}', self.T), '/d/?x=1')

    def test_strip_token_many_links(self):
        text = f'![a](/d/?t={self.T}) and ![b](/e/?t={self.T})'
        self.assertNotIn(self.T, strip_token(text, self.T))

    def test_strip_token_leaves_other_tokens(self):
        self.assertEqual(strip_token('/d/?t=other', self.T), '/d/?t=other')

    def test_strip_token_empty_token(self):
        self.assertEqual(strip_token('/d/?t=', ''), '/d/?t=')


class StripTokenFormsTest(SimpleTestCase):
    T = 'b' * 64

    def test_html_escaped_ampersand(self):
        out = strip_token(f'/d/?x=1&amp;t={self.T}', self.T)
        self.assertNotIn(self.T, out)

    def test_bare_token(self):
        out = strip_token(f'my token {self.T} here', self.T)
        self.assertNotIn(self.T, out)

    def test_url_encoded_inside_other_url(self):
        out = strip_token(f'http://o.example/?u=http%3A%2F%2Fx%2F%3Ft%3D{self.T}', self.T)
        self.assertNotIn(self.T, out)
