import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils.timezone import now

from . import views
from .models import Item

UUID_PATH = r'^/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/$'
ERROR_TEXT = 'No store found for that code. Codes only work for a few minutes; ask for a fresh one.'


class ParseStoreCodeTest(TestCase):
    def test_plain_code(self):
        self.assertEqual(views.parse_store_code('abcd1234'), 'abcd1234')

    def test_whitespace_and_case(self):
        self.assertEqual(views.parse_store_code('  ABcd1234 \n'), 'abcd1234')

    def test_pasted_urls(self):
        for raw in (
            'shortterm.store/abcd1234',
            'https://shortterm.store/abcd1234/',
            'shortterm.store/abcd1234/?x=1#top',
            '/abcd1234/',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(views.parse_store_code(raw), 'abcd1234')

    def test_empty(self):
        for raw in ('', '   ', '/', '///'):
            with self.subTest(raw=raw):
                self.assertEqual(views.parse_store_code(raw), '')


class FindLiveItemTest(TestCase):
    def test_live(self):
        item = Item.objects.create()
        self.assertEqual(views.find_live_item(item.shortcut), item)

    def test_expired(self):
        item = Item.objects.create()
        Item.objects.filter(pk=item.pk).update(created=now() - timedelta(minutes=6))
        self.assertIsNone(views.find_live_item(item.shortcut))

    def test_unknown(self):
        self.assertIsNone(views.find_live_item('00000000'))


@override_settings(NEW_DESIGN_HOSTS=['testserver'])
class LandingTest(TestCase):
    def test_root_renders_landing_on_new_design_host(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'store/landing.html')
        self.assertContains(response, 'action="/open/"')
        self.assertContains(response, 'href="/new/"')
        self.assertNotContains(response, ERROR_TEXT)

    @override_settings(NEW_DESIGN_HOSTS=[])
    def test_root_redirects_on_classic_host(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertRegex(response['Location'], UUID_PATH)

    def test_new_redirects_to_fresh_store(self):
        first = self.client.get('/new/')
        second = self.client.get('/new/')
        self.assertEqual(first.status_code, 302)
        self.assertRegex(first['Location'], UUID_PATH)
        self.assertNotEqual(first['Location'], second['Location'])

    @override_settings(NEW_DESIGN_HOSTS=[])
    def test_new_works_on_classic_host(self):
        response = self.client.get('/new/')
        self.assertEqual(response.status_code, 302)
        self.assertRegex(response['Location'], UUID_PATH)


@override_settings(NEW_DESIGN_HOSTS=['testserver'])
class OpenStoreTest(TestCase):
    def setUp(self):
        self.item = Item.objects.create()

    def assert_opens(self, code):
        response = self.client.get('/open/', {'code': code})
        self.assertEqual(response.status_code, 302, code)
        self.assertEqual(response['Location'], f'/{self.item.guid}/')

    def assert_not_found(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, 'store/landing.html')
        self.assertContains(response, ERROR_TEXT, status_code=404)
        self.assertContains(response, 'id="code-error"', status_code=404)

    def test_live_code(self):
        self.assert_opens(self.item.shortcut)

    def test_uppercase_and_whitespace(self):
        self.assert_opens(f'  {self.item.shortcut.upper()} ')

    def test_pasted_url(self):
        self.assert_opens(f'https://shortterm.store/{self.item.shortcut}/')

    def test_pasted_url_with_query_and_fragment(self):
        self.assert_opens(f'shortterm.store/{self.item.shortcut}/?x=1#top')

    def test_pasted_store_link_opens_that_store(self):
        self.assert_opens(f'https://shortterm.store/{self.item.guid}/')

    def test_expired_code(self):
        Item.objects.filter(pk=self.item.pk).update(created=now() - timedelta(minutes=6))
        response = self.client.get('/open/', {'code': self.item.shortcut})
        self.assert_not_found(response)
        self.assertContains(response, f'value="{self.item.shortcut}"', status_code=404)

    def test_unknown_and_junk(self):
        for code in ('00000000', 'nope', 'ünïcødé', 'x' * 500, f'{uuid.uuid4()}'[:35] + 'z'):
            with self.subTest(code=code[:20]):
                self.assert_not_found(self.client.get('/open/', {'code': code}))

    def test_empty_and_missing(self):
        self.assert_not_found(self.client.get('/open/', {'code': ''}))
        self.assert_not_found(self.client.get('/open/'))

    def test_submitted_value_is_escaped(self):
        response = self.client.get('/open/', {'code': '"><script>x</script>'})
        self.assert_not_found(response)
        self.assertNotContains(response, '<script>x</script>', status_code=404)
        self.assertContains(response, 'value="&quot;&gt;&lt;script&gt;', status_code=404)


@override_settings(NEW_DESIGN_HOSTS=['testserver'])
class LandingContentTest(TestCase):
    def test_copy_and_head(self):
        response = self.client.get('/')
        for text in (
            '<title>shortterm.store</title>',
            'From this device<br>to that one.',
            'Paste text or drop files into a store, then open its short code on any other device.',
            '<meta name="description"',
            '<meta property="og:title" content="shortterm.store">',
            '<meta property="og:description"',
            'text · images · files · optional password',
            'New store →',
            'placeholder="abcd1234"',
            'autocapitalize="off"',
            'spellcheck="false"',
            'localStorage.getItem(\'darkMode\')',
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)

    def test_leaves_out_dropped_copy(self):
        response = self.client.get('/')
        self.assertNotContains(response, 'No account')
        self.assertNotContains(response, 'hour')
        self.assertNotContains(response, 'autofocus')

    def test_error_state_marks_the_field(self):
        response = self.client.get('/open/', {'code': 'nope'})
        self.assertContains(response, 'aria-invalid="true"', status_code=404)
        self.assertContains(response, 'aria-describedby="code-error"', status_code=404)
        self.assertContains(response, 'class="code has-error"', status_code=404)
