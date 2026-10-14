# Landing page

## Goal

Visitors who land on `/` without context should understand what shortterm.store
is and be able to start a store, or open one from a short code, in one action.
Today `/` redirects straight into a new, empty store, which explains nothing.

The page tells one story: moving text and files from one device to another.
It does not mention that stores are deleted after an hour of inactivity.

## Routes

| URL | Before | After |
|---|---|---|
| `/` | Redirect to new `/<uuid>/` | Landing page (new-design hosts only) |
| `/new/` | — | Redirect to new `/<uuid>/` |
| `/open/?code=…` | — | Redirect to the store, or landing page with an error |
| `/<8-char shortcut>/`, `/<uuid>/` | unchanged | unchanged |

- The landing page is served only on hosts in `NEW_DESIGN_HOSTS`. On other
  hosts `/` keeps redirecting to a new store, so classic users see no change.
- `/new/` and `/open/` work on every host.
- `new/` and `open/` must be routed before `<item_shortcut>/` in
  `store/urls.py`. Neither is 8 or 36 characters, so they cannot clash with a
  shortcut or a UUID, but route order keeps that explicit.

### `/open/`

A plain `GET` form; no JavaScript needed.

1. Read `code`, strip whitespace, lowercase it.
2. If it contains `/`, keep only the last non-empty path segment, so pasting
   `https://shortterm.store/abcd1234/` or `shortterm.store/abcd1234` works.
3. If the result is an 8-character shortcut of an item created in the last
   5 minutes (same rule as `item_detail`), redirect to `/<guid>/`.
4. Otherwise render the landing page with status 404, the submitted value kept
   in the input, and the error:
   *"No store found for that code. Codes only work for a few minutes; ask for a fresh one."*

The shortcut lookup is shared with `item_detail` rather than duplicated.

## Page

Layout A from brainstorming: everything centred on one screen, no scrolling.

- **Wordmark** `shortterm.store` top left, monospace, as on the store page.
- **Headline** (Instrument Serif): "From this device / to that one."
- **Sentence**: "Paste text or drop files into a store, then open its short code on any other device."
- **Actions**, in a row: `New store →` (signal-coloured link to `/new/`), the
  word "or", then the code form: a monospace field with a `/` prefix,
  placeholder `abcd1234`, `maxlength` long enough to accept a pasted URL,
  `autocapitalize="off"`, `autocomplete="off"`, `spellcheck="false"`, no
  autofocus, and an `Open` submit button.
- **Error** (only after a failed `/open/`): danger-coloured monospace line under
  the field, field border in the danger colour.
- **Footer line**, small and muted: "text · images · files · optional password".
- **Background**: the grid-paper background of the store page.
- **Phone** (narrow widths): actions stack full width, New store first, then
  "or", then the code form. 16px side gutter, no horizontal scroll.
- **Theme**: the store page's colour tokens and fonts. Follows
  `prefers-color-scheme`, and honours the `darkMode` value (`on`/`off`/`auto`)
  the store page saves in `localStorage`, applied before first paint. No toggle
  on the landing page.
- **Head**: `<title>shortterm.store</title>`, a meta description using the
  sentence above, and Open Graph title/description tags.

New template: `store/templates/store/landing.html`, self-contained like
`item_detail.html` (inline CSS, same Google Fonts link). The colour tokens are
copied, not shared; extracting a shared stylesheet is out of scope.

## Tests

In `store/tests.py` (or a new `store/test_landing.py`):

- `/` on a new-design host renders the landing page (200, template used).
- `/` on a classic host redirects to a `/<uuid>/` URL.
- `/new/` redirects to a `/<uuid>/` URL.
- `/open/` with a live shortcut redirects to that item.
- `/open/` with uppercase or whitespace around a live shortcut still redirects.
- `/open/` with a pasted full URL ending in a live shortcut redirects.
- `/open/` with an expired shortcut, an unknown code, junk, or an empty value
  returns 404 with the landing page, the error text and the submitted value.

## Out of scope

- Recent stores, accounts, analytics.
- Any mention of expiry beyond the bad-code error.
- Changes to the store page or the classic design.
