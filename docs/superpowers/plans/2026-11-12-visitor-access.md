# Visitor Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner of a password-protected store choose what people without the password can do: nothing (Closed), view and download (Read-only), or also add files without overwriting (Drop box).

**Architecture:** A new `Item.visitor_access` field and a small `store/access.py` module that classifies every request as owner, read, dropbox or no access. Each view in `store/views.py` gates on that one answer. Drop box uploads get a free numbered filename and a signed "no replace" flag that the storage layer enforces atomically. The new-design page (`item_detail.html`) gets a visitor view and a three-way switch in the password dialog; the classic page is untouched and its hosts keep visitors locked.

**Tech Stack:** Django, Postgres, Vue 3 (CDN, no build step), marked + DOMPurify.

**Spec:** `docs/superpowers/specs/2026-11-12-visitor-access-design.md`. Mockups: `.superpowers/brainstorm/*/content/access-modes.html` (dialog option B) and `access-modes-v2.html` (visitor view).

## Global Constraints

- Run tests in Docker: `docker compose run --rm app python manage.py test store` (no local Postgres; the local virtualenv has the wrong Django).
- `visitor_access` values exactly `closed`, `read`, `dropbox`; default `closed`; existing rows stay `closed`.
- The owner token (`t` / `password_token`) is never sent to a visitor, in any field or inside text.
- Read-only / Drop box apply only when `request.get_host()` is in `settings.NEW_DESIGN_HOSTS`. On other hosts, visitors get exactly today's 403 `locked`.
- Owner behaviour is unchanged everywhere, including owner uploads replacing a file of the same name.
- New test classes that exercise visitor access use `@override_settings(NEW_DESIGN_HOSTS=['testserver'])` (the Django test client's host).
- Numbered copies: `IMG_0042.jpg` → `IMG_0042 (2).jpg`, then `(3)`, … (number before the last extension; no extension → `README (2)`).
- No banners in the visitor view.
- Copy, verbatim:
  - Tooltip, Read-only: `Read-only. You can view and download, but not change anything.`
  - Tooltip, Drop box: `Drop box. You can view, download and add files, but not change or delete what's here.`
  - Dialog label: `Visitors without password`; switch labels `Closed`, `Read-only`, `Drop box`; dialog heading `Protect this store`; buttons `Remove`, `Cancel`, `Save`.
  - Hints: Closed `Only people with the password can see this store.`; Read-only `They can view and download, but can't change or delete anything.`; Drop box `They can view, download and add files, but can't change or delete what's here.`
  - Header button: `Unlock to edit`.

## Review Focus

- **A visitor whose browser holds a stale password** (the owner changed it) must get the visitor view of a Read-only store, not the locked screen. Test in Task 2: `test_wrong_password_header_gets_visitor_view`.
- **Token in text in every URL shape:** `?t=TOK`, `?t=TOK&x=1`, `?x=1&t=TOK`, several links in one text. Test in Task 1: `test_strip_token_*`.
- **A name taken only on disk, or only in the database,** still gets a numbered copy for a visitor. Test in Task 3: `test_dropbox_numbers_name_taken_on_disk_only` and `..._in_db_only`.
- **A numbered name that gets too long** answers 400, not 500. Test in Task 3: `test_dropbox_numbered_name_too_long`.
- **The owner switches to Closed while a visitor has the page open:** the next poll returns 403 and the page must fall back to the locked screen, with no visitor controls left over. Server side is Task 2's `test_closed_visitor_json_locked`; the page side is a check in Task 6.

---

### Task 1: Field, migration and access rules

**Files:**
- Modify: `store/models.py` (add field to `Item`)
- Create: `store/migrations/0006_item_visitor_access.py` (via `makemigrations`)
- Create: `store/access.py`
- Test: `store/test_access.py`

**Interfaces:**
- Produces, in `store/models.py`: `Item.visitor_access = models.CharField(max_length=16, choices=VISITOR_ACCESS_CHOICES, default='closed')`, with module constants `CLOSED = 'closed'`, `READ = 'read'`, `DROPBOX = 'dropbox'`, `VISITOR_ACCESS_CHOICES`.
- Produces, in `store/access.py`:
  - `OWNER = 'owner'` (alongside re-exported `READ`, `DROPBOX`)
  - `uses_new_design(request) -> bool`: `request.get_host() in settings.NEW_DESIGN_HOSTS`.
  - `access_for(item, request) -> str | None`: `OWNER` if `views.check_item_password` passes (move `check_item_password` and `get_item_password_token` into this module and import them back into `views.py`); else `item.visitor_access` if it is `READ`/`DROPBOX` and `uses_new_design(request)`; else `None`.
  - `strip_token(text: str, token: str) -> str`: removes the `t=<token>` query parameter wherever it appears, keeping the rest of each URL valid. Returns `text` unchanged when `token` is empty.

- [ ] **Step 1: Write the failing tests** in `store/test_access.py`:

```python
@override_settings(NEW_DESIGN_HOSTS=['testserver'])
class AccessForTest(TestCase):
    # build requests with RequestFactory().get('/', HTTP_X_ITEM_PASSWORD=...)
    def test_no_password_is_owner(self): ...            # access_for == OWNER
    def test_correct_password_is_owner(self): ...       # visitor_access='read', header 'pw' -> OWNER
    def test_closed_visitor_has_no_access(self): ...    # -> None
    def test_read_visitor(self): ...                    # -> READ
    def test_dropbox_visitor(self): ...                 # -> DROPBOX
    def test_wrong_password_falls_back_to_visitor(self): ...  # header 'nope', read -> READ
    def test_classic_host_visitor_has_no_access(self): ...    # HTTP_HOST='localhost' with ALLOWED_HOSTS=['*'], read -> None

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
```

- [ ] **Step 2: Run to verify they fail**
Run: `docker compose run --rm app python manage.py test store.test_access`
Expected: errors importing `store.access`.

- [ ] **Step 3: Add the field and generate the migration**
Run: `docker compose run --rm app python manage.py makemigrations store -n item_visitor_access`
Expected: creates `store/migrations/0006_item_visitor_access.py` with an `AddField` defaulting to `'closed'`.

- [ ] **Step 4: Implement `store/access.py`** as specified in Interfaces. Moving `check_item_password` / `get_item_password_token` must not change their behaviour; `views.py` imports them from `access`.

- [ ] **Step 5: Run the new tests and the whole suite**
Run: `docker compose run --rm app python manage.py test store`
Expected: all pass.

- [ ] **Step 6: Commit**
```bash
git add store/models.py store/migrations/0006_item_visitor_access.py store/access.py store/views.py store/test_access.py
git commit -m "Add visitor access setting and request access rules"
```

---

### Task 2: Gate the store page, downloads and deletes

**Files:**
- Modify: `store/views.py` (`item_detail`, `upload_download`, `upload_delete`)
- Test: `store/test_visitor_views.py`

**Interfaces:**
- Consumes: `access_for`, `OWNER`, `READ`, `DROPBOX`, `strip_token`, `get_item_password_token` from Task 1.
- Produces: store JSON gains `viewer` (`'owner'` or `'visitor'`) and `visitor_access` (the stored value) on every 200 response. For visitors: `t` and `password_token` are `''`, `text` has the token stripped, upload URLs carry no token. The 403 body is unchanged.
- Produces: store POST accepts `visitor_access` (one of the three values, else 400 `{"error": "invalid visitor_access"}`), alone or with other fields.

- [ ] **Step 1: Write the failing tests** in `store/test_visitor_views.py` (`TempStorageMixin, TestCase`, `@override_settings(NEW_DESIGN_HOSTS=['testserver'])`; setUp makes an item with password `pw`, text containing `?t=<its token>`, and one upload with a file on disk):

```python
def test_read_visitor_json(self):          # 200, viewer 'visitor', visitor_access 'read', t == password_token == ''
def test_read_visitor_text_has_no_token(self):   # token string not anywhere in response.content
def test_dropbox_visitor_json(self):       # 200, viewer 'visitor'
def test_closed_visitor_json_locked(self): # 403, body {'has_password': True, 'error': 'locked', ...} as today
def test_wrong_password_header_gets_visitor_view(self):  # X-Item-Password 'old', read -> 200 viewer 'visitor'
def test_owner_json_unchanged(self):       # header 'pw': viewer 'owner', t non-empty, text still contains token
def test_classic_host_visitor_locked(self):  # HTTP_HOST='localhost', ALLOWED_HOSTS=['*'], read -> 403
def test_visitor_cannot_post(self):        # for read and dropbox: POST {'text': 'x'}, {'name': 'x'}, {'mode': 'reader'},
                                           # {'password': ''}, {'visitor_access': 'closed'} each -> 403, item unchanged
def test_owner_sets_visitor_access_alone(self):  # POST {'visitor_access': 'dropbox'} with header -> 200; password hash unchanged
def test_owner_invalid_visitor_access(self):     # POST {'visitor_access': 'public'} -> 400
def test_visitor_download(self):           # read and dropbox -> 200 (X-Accel-Redirect set); closed -> 403
def test_visitor_cannot_delete(self):      # read and dropbox -> 403, file and row still there
def test_owner_delete_still_works(self):   # header 'pw' -> 200
```

- [ ] **Step 2: Run to verify they fail**
Run: `docker compose run --rm app python manage.py test store.test_visitor_views`
Expected: FAIL (visitors currently get 403 everywhere; `viewer` missing).

- [ ] **Step 3: Implement** in `store/views.py`: compute `access = access_for(item, request)` once per view. `item_detail` POST and `upload_delete` require `OWNER`; JSON GET and `upload_download` require any non-`None` access. Validate `visitor_access` against the model choices before saving anything in the POST.

- [ ] **Step 4: Run the whole suite**
Run: `docker compose run --rm app python manage.py test store`
Expected: all pass, including the existing `PasswordProtectionTest`.

- [ ] **Step 5: Commit**
```bash
git add store/views.py store/test_visitor_views.py
git commit -m "Let visitors view and download read-only and drop box stores"
```

---

### Task 3: Drop box uploads that never overwrite

**Files:**
- Modify: `store/storage.py` (`save`, new `free_key_for`)
- Modify: `store/views.py` (`generate_upload_params`, `upload_receive`, `upload`)
- Test: `store/test_dropbox_uploads.py`

**Interfaces:**
- Consumes: `access_for`, `OWNER`, `DROPBOX` from Task 1.
- Produces, in `store/storage.py`:
  - `save(key, uploaded_file, replace=True)`: with `replace=False`, raises `FileExistsError` if the target exists, atomically (link the finished temp file into place with `os.link`, then remove the temp file; never `os.replace`/`shutil.move` onto the target).
  - `free_key_for(item_guid, filename, taken: set[str]) -> str`: `key_for` of the cleaned name if that key is not in `taken` and not on disk, else the first free numbered name per Global Constraints. Raises `InvalidKey` if the numbered name is too long.
- Produces: the upload token payload becomes `{'key': key, 'replace': bool}`. `upload_receive` still accepts an old plain-string token as `replace=True` (they were only ever issued to owners and live up to 24 h across a deploy).
- Produces: `upload_receive` returns 409 `{"error": "exists"}` when a no-replace upload hits an existing file.
- Produces: `upload` (step 3) for a Drop box visitor returns 409 if an `Upload` row for that key already exists (no `modified` touch) and 400 if the file is not on disk.

- [ ] **Step 1: Write the failing tests** in `store/test_dropbox_uploads.py` (reuse the helpers style of `UploadFlowTest`; `@override_settings(NEW_DESIGN_HOSTS=['testserver'])`; item with password `pw`):

```python
def test_dropbox_visitor_full_flow(self):          # visitor uploads 'a.txt' (free) -> key '<guid>/a.txt', 204, row created
def test_dropbox_visitor_gets_numbered_copy(self): # 'IMG_0042.jpg' exists -> key '<guid>/IMG_0042 (2).jpg'; original bytes untouched
def test_dropbox_numbering_counts_up(self):        # 'a.txt' and 'a (2).txt' exist -> 'a (3).txt'
def test_dropbox_no_extension(self):               # 'README' exists -> 'README (2)'
def test_dropbox_numbers_name_taken_on_disk_only(self):  # file on disk, no row -> numbered
def test_dropbox_numbers_name_taken_in_db_only(self):    # row, no file -> numbered
def test_dropbox_numbered_name_too_long(self):     # existing name whose key is exactly 255 chars (218-char name) -> numbered key too long -> 400
def test_dropbox_receive_refuses_overwrite(self):  # get params, then create the file before sending -> 409, file unchanged
def test_dropbox_register_existing_row_conflict(self):  # step 3 with an existing key -> 409, modified unchanged
def test_dropbox_register_requires_file(self):     # step 3 for a key with no file -> 400
def test_read_visitor_cannot_upload(self):         # visitor_access 'read' -> params 403, step 3 403
def test_closed_visitor_cannot_upload(self):       # 'closed' -> 403
def test_classic_host_dropbox_visitor_cannot_upload(self):  # HTTP_HOST='localhost' -> 403
def test_owner_still_replaces(self):               # header 'pw', same name twice -> same key, second bytes win
def test_old_string_token_still_replaces(self):    # signing.dumps(key, salt=UPLOAD_TOKEN_SALT) -> 204, overwrites
```

- [ ] **Step 2: Run to verify they fail**
Run: `docker compose run --rm app python manage.py test store.test_dropbox_uploads`
Expected: FAIL.

- [ ] **Step 3: Implement `save(..., replace=False)` and `free_key_for`** in `store/storage.py`. `taken` is the set of this item's `Upload.key` values, passed in by the view.

- [ ] **Step 4: Implement the view changes**: `generate_upload_params` allows `OWNER` and `DROPBOX`; visitors get `free_key_for(...)` and `replace=False` in the signed payload. `upload_receive` reads the payload, passes `replace` to `storage.save`, maps `FileExistsError` to 409. `upload` allows `OWNER` and `DROPBOX` with the visitor rules above.

- [ ] **Step 5: Run the whole suite**
Run: `docker compose run --rm app python manage.py test store`
Expected: all pass, including `test_receive_overwrites_same_name`.

- [ ] **Step 6: Commit**
```bash
git add store/storage.py store/views.py store/test_dropbox_uploads.py
git commit -m "Let drop box visitors add files as numbered copies, never overwriting"
```

---

### Task 4: Keep the token out of store text

**Files:**
- Modify: `store/templates/store/item_detail.html` (`handlePaste`, `renderedMarkdown`)

**Interfaces:**
- Consumes: `passwordToken` (already in the Vue data).
- Produces: `withOwnToken(html: string, token: string) -> string` (plain function in the page script): for every `src`/`href` in the rendered HTML that starts with `'/' + ITEM_GUID + '/upload/'` and has no `t=` parameter, appends `t=<token>`. Returns `html` unchanged when `token` is empty.

- [ ] **Step 1: Stop inserting the token on paste.** In `handlePaste`, the inserted link is `data.url` with no `?t=`.

- [ ] **Step 2: Add the token at render time.** `renderedMarkdown` returns `withOwnToken(DOMPurify.sanitize(marked.parse(this.text)), this.passwordToken)`. Use a `<template>` element or a DOMPurify `afterSanitizeAttributes` hook, not regex over HTML.

- [ ] **Step 3: Check in the browser** (start the app as in Task 6, Step 1): as owner of a Closed store, paste an image into the text, then switch to READ. Expected: the saved text has no `t=`; the image shows; `onPreviewClick` still opens it in the lightbox.

- [ ] **Step 4: Commit**
```bash
git add store/templates/store/item_detail.html
git commit -m "Add the owner token to own image links at render time, not into the text"
```

---

### Task 5: Visitor view and password dialog

**Files:**
- Modify: `store/templates/store/item_detail.html`

**Interfaces:**
- Consumes: JSON `viewer` and `visitor_access` (Task 2); upload rules (Task 3).
- Produces (Vue data/computed): `viewer` (`'owner'`), `visitorAccess` (`'closed'`), `newVisitorAccess`; `isVisitor = !locked && viewer === 'visitor'`; `canEdit = !locked && !isVisitor`; `canUpload = !locked && (!isVisitor || visitorAccess === 'dropbox')`.
- Produces (method): `saveProtection()` posts `{visitor_access: newVisitorAccess}` plus `password: newPassword` only when `newPassword` is non-empty; on success stores the new password in localStorage if one was typed, closes the dialog, toasts, calls `update()`. `setPassword('')` stays for **Remove**.

- [ ] **Step 1: Read state from the server.** `applyServerState` sets `viewer` and `visitorAccess`. The 403 branch of `update()` resets `viewer` to `'owner'` so a store switched to Closed shows only the locked screen.

- [ ] **Step 2: Visitor header.** When `isVisitor`, the control cell shows, in place of the mode switch and password button: a lock icon (`#i-lock`, signal colour) with a hover/focus tooltip in the style of mockup v2 carrying the Global Constraints tooltip copy (also as its `aria-label`), an `Unlock to edit` button, and the theme button.

- [ ] **Step 3: Unlock dialog.** `Unlock to edit` opens a `<dialog>` with the same fields as the locked-screen form, sharing `inputPassword`, `unlockError` and `verifyPassword()`; on success it closes and the page becomes the owner view without a reload.

- [ ] **Step 4: Read-only body.** Title input and textarea are `readonly` unless `canEdit`; `handlePaste` and the `=` calculator do nothing unless `canEdit`; the empty-text hints show only when `canEdit`; delete buttons (tiles and list) render only when `canEdit`; the add tile/row, file picker, drag overlay and drop handling work only when `canUpload`. `saveLabel` returns `'Read-only'` or `'Drop box'` for visitors.

- [ ] **Step 5: Password dialog (mockup option B).** Heading `Protect this store`; label `Password set`/`No password` as today; the password field; label `Visitors without password` with a `Closed | Read-only | Drop box` segmented control (reuse `.seg`) and the matching hint line; buttons `Remove` (only with a password), `Cancel`, `Save`. `openPasswordDialog` sets `newVisitorAccess` to `visitorAccess` when a password exists, else `'read'`. The switch is disabled while `!hasPassword && !newPassword`. Save is disabled in that same state. Submit calls `saveProtection()`.

- [ ] **Step 6: Commit**
```bash
git add store/templates/store/item_detail.html
git commit -m "Add the visitor view and the visitor access switch to the store page"
```

---

### Task 6: Check it end to end

- [ ] **Step 1: Start the app on a new-design host.** The new page is only served for hosts in `NEW_DESIGN_HOSTS`, and `docker-compose.yml` does not pass that variable, so set it for this run only, e.g. `docker compose run --rm --service-ports -e NEW_DESIGN_HOSTS=localhost app`, and open `http://localhost:8400/new/`. Don't change `docker-compose.yml` or `.env`. (Downloads go through nginx in production, so file bytes and inline images may not load locally; the controls and status codes can still be checked.)

- [ ] **Step 2: Walk through it in the browser** with two profiles (owner tab, and a private window as visitor):
  1. Owner: set a password; the switch starts at Read-only; Save. Visitor: sees text and files, lock icon with the Read-only tooltip, no delete buttons, text not editable, no add tile, status `Read-only`.
  2. Owner: open the dialog, leave the field empty, pick Drop box, Save. Expected: the password still works. Visitor (on the next poll): add tile is back; uploading a file with an existing name gives `name (2).ext`.
  3. Visitor: `Unlock to edit` with the right password gives the full owner view without a reload; with a wrong one, the error shows.
  4. Owner: switch to Closed. Visitor (on the next poll): only the locked screen, no lock icon or Unlock button left over.
  5. Owner: `Remove`. Visitor: the full open store, as today.
  6. With `localhost` not in `NEW_DESIGN_HOSTS` (restart without `-e`), a visitor of a Read-only store sees the classic page's locked screen.

- [ ] **Step 3: Run the full suite**
Run: `docker compose run --rm app python manage.py test store`
Expected: all pass.
