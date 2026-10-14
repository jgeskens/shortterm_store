# Landing Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve a one-screen landing page on `/` (new-design hosts only) with a "New store" button and a box to open a store by its short code.

**Architecture:** Three small views in `store/views.py`: `landing` (`/`), `new_store` (`/new/`) and `open_store` (`/open/`), plus two helpers, `find_live_item` (shared with `item_detail`) and `parse_store_code`. One new self-contained template, `store/templates/store/landing.html`, styled with the colour tokens and fonts of `item_detail.html`. No JavaScript apart from the pre-paint theme snippet.

**Tech Stack:** Django 6 (in Docker), Postgres in Docker for tests, plain HTML/CSS.

**Spec:** `docs/superpowers/specs/2026-10-14-landing-page-design.md`

## Global Constraints

- Run tests in Docker: `docker compose run --rm app python manage.py test store` (no local Postgres; the local virtualenv has the wrong Django).
- Landing page only when `request.get_host() in settings.NEW_DESIGN_HOSTS`; otherwise `/` redirects to `/<new uuid>/` exactly as today.
- Shortcut lifetime: an item's 8-character `shortcut` is valid while `created >= now() - 5 minutes` (same rule `item_detail` uses today).
- Headline copy: `From this device<br>to that one.`
- Sentence copy: `Paste text or drop files into a store, then open its short code on any other device.`
- Error copy: `No store found for that code. Codes only work for a few minutes; ask for a fresh one.`
- Footer copy: `text · images · files · optional password`
- Button copy: `New store →` and `Open`; separator word `or`; input placeholder `abcd1234`.
- No mention of the one-hour inactivity cleanup anywhere on the page. No "No account, no app."
- Commit messages: short imperative sentence in the style of `git log` (e.g. "Add design spec for a landing page on /"), no prefixes, no Co-Authored-By or "Generated with" lines.

## Review Focus

- A pasted link with a query string or fragment (`shortterm.store/abcd1234/?x=1#top`) should still open the store.
- A pasted full store link (`https://shortterm.store/<uuid>/`) should open that store, not show "No store found".
- Long or non-ASCII input (`ünïcødé`, 500 characters) should give the 404 landing page, never a 500.
- HTML in the code (`"><script>`) must come back escaped inside the input's `value`.
- `/open/` with no `code` parameter at all behaves like an empty code (404 with the error), not a crash.

---

## File Structure

| File | Responsibility |
|---|---|
| `store/views.py` | Add `find_live_item`, `parse_store_code`, `landing`, `new_store`, `open_store`; make `item_detail` use `find_live_item` and drop its now-dead empty-shortcut branch |
| `store/urls.py` | Route `''` → `landing`, `new/` → `new_store`, `open/` → `open_store`, before `<item_shortcut>/` |
| `store/templates/store/landing.html` | The landing page (minimal in Task 1, full design in Task 2) |
| `store/test_landing.py` | All tests for this feature |

---

### Task 1: Routes, views and the code lookup

**Files:**
- Modify: `store/views.py` (helpers above `item_detail`, new views below it, `item_detail` lines 78–83)
- Modify: `store/urls.py`
- Create: `store/templates/store/landing.html` (minimal; replaced in Task 2)
- Create: `store/test_landing.py`

**Interfaces:**
- Consumes: `models.Item` (`guid: UUID`, `shortcut: str` of 8 lowercase hex chars, `created: datetime`), `settings.NEW_DESIGN_HOSTS: list[str]`.
- Produces:
  - `views.find_live_item(shortcut: str) -> models.Item | None`
  - `views.parse_store_code(raw: str) -> str` (lowercased last path segment, `''` if none)
  - `views.landing(request)`, `views.new_store(request)`, `views.open_store(request)`
  - Template `store/landing.html` rendered with context `{'code': str, 'error': bool}`; it must contain a `<form action="/open/" method="get">` with `<input name="code" value="{{ code }}">`, a link to `/new/`, and, when `error` is true, an element with `id="code-error"` holding the error copy.

- [ ] **Step 1: Write the failing tests**

Create `store/test_landing.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm app python manage.py test store.test_landing`
Expected: errors such as `AttributeError: module 'store.views' has no attribute 'parse_store_code'`.

- [ ] **Step 3: Add the helpers to `store/views.py`**

Insert directly above `def item_detail(`:

```python
SHORTCUT_LIFETIME = timedelta(minutes=5)


def find_live_item(shortcut: str):
    """The item a short code points to, if it was created recently enough."""
    return (models.Item.objects
            .filter(shortcut=shortcut, created__gte=now() - SHORTCUT_LIFETIME)
            .order_by('-created')
            .first())


def parse_store_code(raw: str) -> str:
    """Turn what someone typed or pasted into the code box into a bare code."""
    value = raw.strip().lower().split('?', 1)[0].split('#', 1)[0]
    segments = [segment for segment in value.split('/') if segment]
    return segments[-1] if segments else ''
```

- [ ] **Step 4: Make `item_detail` use `find_live_item`**

Replace the start of `item_detail` (currently lines 78–83):

```python
def item_detail(request, item_shortcut=''):
    if len(item_shortcut) == 0:
        return redirect(f'/{uuid.uuid4()}/')
    elif len(item_shortcut) == 8:
        item = get_object_or_404(models.Item, shortcut=item_shortcut, created__gte=now() - timedelta(minutes=5))
        return redirect(f'/{item.guid}/')
    elif len(item_shortcut) == 36:
```

with:

```python
def item_detail(request, item_shortcut):
    if len(item_shortcut) == 8:
        item = find_live_item(item_shortcut)
        if item is None:
            raise Http404
        return redirect(f'/{item.guid}/')
    elif len(item_shortcut) == 36:
```

(`''` is now routed to `landing`, so the empty branch is dead. Keep the `get_object_or_404` import; the upload views still use it.)

- [ ] **Step 5: Add the three views**

Append below `item_detail` (above `generate_upload_params`):

```python
def new_store(request):
    return redirect(f'/{uuid.uuid4()}/')


def landing(request):
    if request.get_host() not in settings.NEW_DESIGN_HOSTS:
        return new_store(request)
    return render(request, 'store/landing.html', {'code': '', 'error': False})


def open_store(request):
    raw = request.GET.get('code', '')
    code = parse_store_code(raw)
    if len(code) == 8:
        item = find_live_item(code)
        if item is not None:
            return redirect(f'/{item.guid}/')
    elif len(code) == 36:
        try:
            return redirect(f'/{uuid.UUID(code)}/')
        except ValueError:
            pass
    return render(request, 'store/landing.html', {'code': raw.strip(), 'error': True}, status=404)
```

- [ ] **Step 6: Route them in `store/urls.py`**

Replace the `urlpatterns` list with:

```python
urlpatterns = [
    path('new/', views.new_store),
    path('open/', views.open_store),
    path('<item_guid>/generate-upload-params/', views.generate_upload_params),
    path('<item_guid>/upload/receive/', views.upload_receive, name='upload_receive'),
    path('<item_guid>/upload/', views.upload),
    path('<item_guid>/verify-password/', views.verify_password),
    path('<item_guid>/upload/<upload_guid>/download/', views.upload_download, name='upload_download'),
    path('<item_guid>/upload/<upload_guid>/delete/', views.upload_delete),
    path('<item_shortcut>/', views.item_detail),
    path('', views.landing),
]
```

- [ ] **Step 7: Create the minimal template**

Create `store/templates/store/landing.html` (Task 2 replaces it with the full design):

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>shortterm.store</title>
</head>
<body>
    <a href="/new/">New store →</a>
    <form action="/open/" method="get">
        <input name="code" value="{{ code }}" placeholder="abcd1234" aria-label="Store code">
        <button type="submit">Open</button>
        {% if error %}<p id="code-error">No store found for that code. Codes only work for a few minutes; ask for a fresh one.</p>{% endif %}
    </form>
</body>
</html>
```

- [ ] **Step 8: Run the new tests**

Run: `docker compose run --rm app python manage.py test store.test_landing`
Expected: all tests pass.

- [ ] **Step 9: Run the whole suite**

Run: `docker compose run --rm app python manage.py test store`
Expected: `OK`. If an existing test fetched `/` expecting a redirect, it now runs on host `testserver`, which is not in the default `NEW_DESIGN_HOSTS`, so it should still get the redirect. If one fails anyway, read it before changing anything.

- [ ] **Step 10: Commit**

```bash
git add store/views.py store/urls.py store/templates/store/landing.html store/test_landing.py
git commit -m "Add landing, new-store and open-by-code routes"
```

---

### Task 2: Landing page design

**Files:**
- Modify (full rewrite): `store/templates/store/landing.html`
- Modify: `store/test_landing.py` (add `LandingContentTest`)

**Interfaces:**
- Consumes: context `{'code': str, 'error': bool}` from Task 1; routes `/new/` and `/open/`; the colour tokens in `store/templates/store/item_detail.html` lines 21–73 and the theme snippet at lines 11–18.
- Produces: the final page. Nothing downstream depends on it.

- [ ] **Step 1: Write the failing content tests**

Append to `store/test_landing.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose run --rm app python manage.py test store.test_landing.LandingContentTest`
Expected: FAIL. The minimal template has no meta tags, headline or footer.

- [ ] **Step 3: Replace `store/templates/store/landing.html`**

Copy the `:root` token block, the dark-mode `@media` block and the `:root[data-theme="dark"]` block **verbatim** from `item_detail.html` lines 21–73 where marked below. Do not retype the values.

```html
<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>shortterm.store</title>
    <meta name="description" content="Paste text or drop files into a store, then open its short code on any other device.">
    <meta property="og:type" content="website">
    <meta property="og:title" content="shortterm.store">
    <meta property="og:description" content="Paste text or drop files into a store, then open its short code on any other device.">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500&family=Geist+Mono:wght@400;500&family=Instrument+Serif&display=swap" rel="stylesheet">
    <script>
        // Same pre-paint theme switch as the store page, so a saved dark/light choice carries over.
        (function () {
            var mode = 'auto';
            try { mode = localStorage.getItem('darkMode') || 'auto'; } catch (e) {}
            if (mode === 'on') document.documentElement.setAttribute('data-theme', 'dark');
            else if (mode === 'off') document.documentElement.setAttribute('data-theme', 'light');
        })();
    </script>
    <style>
        /* ↓ paste item_detail.html lines 21–73 here: :root tokens, the prefers-color-scheme block, :root[data-theme="dark"] */

        * { box-sizing: border-box; }
        html, body { margin: 0; }
        body {
            min-height: 100vh;
            min-height: 100dvh;
            display: flex;
            flex-direction: column;
            background-color: var(--bg);
            background-image:
                linear-gradient(var(--grid) 1px, transparent 1px),
                linear-gradient(90deg, var(--grid) 1px, transparent 1px);
            background-size: 24px 24px;
            background-position: -1px -1px;
            color: var(--ink);
            font: 14px/1.6 var(--sans);
            -webkit-font-smoothing: antialiased;
        }
        :focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; }

        .brand {
            align-self: flex-start;
            padding: 20px 28px;
            font: 13px/1 var(--mono);
            color: var(--ink-2);
            text-decoration: none;
        }
        main {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 20px;
            padding: 24px 28px 48px;
            text-align: center;
        }
        h1 {
            margin: 0;
            font: 400 clamp(46px, 9vw, 88px)/0.95 var(--serif);
            letter-spacing: -0.01em;
        }
        .pitch { margin: 0; max-width: 440px; color: var(--ink-2); font-size: 15px; }

        .actions { display: flex; align-items: flex-start; gap: 12px; margin-top: 8px; }
        .new {
            display: inline-block;
            padding: 11px 18px;
            border-radius: 2px;
            background: var(--signal);
            color: var(--signal-ink);
            font-weight: 500;
            line-height: 20px;
            text-decoration: none;
            white-space: nowrap;
        }
        .or { font: 12px/42px var(--mono); color: var(--ink-3); }

        .open { display: flex; flex-direction: column; gap: 6px; width: 240px; margin: 0; text-align: left; }
        .code {
            display: flex;
            border: 1px solid var(--line-strong);
            border-radius: 2px;
            background: var(--paper);
            font-family: var(--mono);
        }
        .code:focus-within { border-color: var(--signal); }
        .code.has-error { border-color: var(--danger); }
        .code .prefix { padding: 10px 0 10px 12px; color: var(--ink-3); line-height: 20px; }
        .code input {
            flex: 1;
            min-width: 0;
            padding: 10px 4px;
            border: 0;
            background: transparent;
            color: var(--ink);
            font: 14px/20px var(--mono);
            letter-spacing: 0.08em;
        }
        .code input:focus { outline: none; }
        .code input::placeholder { color: var(--ink-3); }
        .code button {
            padding: 0 14px;
            border: 0;
            border-left: 1px solid var(--line-strong);
            background: none;
            color: var(--ink);
            font: 500 14px/20px var(--mono);
            cursor: pointer;
        }
        .code button:hover { color: var(--signal); }
        .error { margin: 0; color: var(--danger); font: 12px/1.5 var(--mono); }

        footer { padding: 0 28px 20px; color: var(--ink-3); font: 11px/1.4 var(--mono); text-align: center; }

        @media (max-width: 600px) {
            .brand { padding: 16px; }
            main { padding: 24px 16px 40px; }
            .actions { flex-direction: column; align-items: stretch; width: 100%; gap: 10px; }
            .new { text-align: center; }
            .or { line-height: 1.4; text-align: center; }
            .open { width: 100%; }
            footer { padding: 0 16px 16px; }
        }
    </style>
</head>

<body>
    <a class="brand" href="/">shortterm.store</a>
    <main>
        <h1>From this device<br>to that one.</h1>
        <p class="pitch">Paste text or drop files into a store, then open its short code on any other device.</p>
        <div class="actions">
            <a class="new" href="/new/">New store →</a>
            <span class="or">or</span>
            <form class="open" action="/open/" method="get">
                <div class="code{% if error %} has-error{% endif %}">
                    <span class="prefix" aria-hidden="true">/</span>
                    <input name="code" value="{{ code }}" placeholder="abcd1234" maxlength="200"
                           aria-label="Store code" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
                           {% if error %}aria-invalid="true" aria-describedby="code-error"{% endif %}>
                    <button type="submit">Open</button>
                </div>
                {% if error %}<p class="error" id="code-error">No store found for that code. Codes only work for a few minutes; ask for a fresh one.</p>{% endif %}
            </form>
        </div>
    </main>
    <footer>text · images · files · optional password</footer>
</body>

</html>
```

Then replace the `/* ↓ paste … */` comment line with the copied lines from `item_detail.html`.

- [ ] **Step 4: Run all landing tests**

Run: `docker compose run --rm app python manage.py test store.test_landing`
Expected: all pass, including Task 1's tests.

- [ ] **Step 5: Check it in the browser**

Start the dev server with the `shortterm-dev` entry in `.claude/launch.json` (it sets `NEW_DESIGN_HOSTS=localhost:8765`). If that entry's script no longer exists, run the app with Docker and set `NEW_DESIGN_HOSTS` to the host you browse on. Check:
- `http://localhost:8765/`: centred headline, sentence, `New store →`, `or`, code box, footer. No horizontal scroll.
- Dark mode (emulate `prefers-color-scheme: dark`): dark tokens apply.
- `/open/?code=nope`: red border, error line under the box, `nope` kept in the field.
- Width 375px: actions stack full width in the order New store, or, code box. 16px side gutter, nothing clipped.
- Click `New store →`: lands on a new `/<uuid>/` store page.
- Create a store, copy its 8-character code, go back to `/`, type it and press Enter: opens that store.

Take a screenshot of the desktop view and the 375px view.

- [ ] **Step 6: Run the whole suite**

Run: `docker compose run --rm app python manage.py test store`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add store/templates/store/landing.html store/test_landing.py
git commit -m "Design the landing page"
```
