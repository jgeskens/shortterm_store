# Visitor access for password-protected stores

## Why

Password protection is all or nothing: once a store has a password, anyone without it sees only "Enter the password", and every read and write is blocked. The owner wants to share files with friends through the link without the friends being able to delete them by accident, and sometimes wants friends to add their own files too. The fully closed store stays useful for private content.

## What it is

A password-protected store gets a **visitor access** setting that decides what people without the password can do:

| Visitor access | View text and files | Download | Add files | Edit text/title, delete files, change settings |
|---|---|---|---|---|
| **Closed** | no | no | no | no |
| **Read-only** | yes | yes | no | no |
| **Drop box** | yes | yes | yes, never overwriting | no |

Anyone with the password (the "owner") can do everything, as today. A store without a password stays fully open to everyone, as today. Visitor access only matters when a password is set.

## Goals

- The owner picks Closed, Read-only or Drop box when setting or changing the password.
- Visitors in Read-only and Drop box see the store's text and files and can download them.
- In Drop box, visitors can add files. A visitor upload never replaces an existing file. When the name is taken, the file is stored as a numbered copy: `IMG_0042.jpg` → `IMG_0042 (2).jpg`, then `(3)`, and so on.
- The owner can get from the visitor view to full editing with an **Unlock to edit** button that asks for the password.
- Every restriction is enforced on the server. Hiding buttons is only the UI side of it.

## Non-goals

- Separate passwords or roles per person, accounts, or invite lists.
- Visitors editing the text in any mode.
- Visitor access for stores without a password.
- Changing how the owner's uploads behave: an owner upload with an existing name still replaces that file, as today.

## Anti-goals (failure even if it "works")

- **A visitor gets owner powers.** In particular, the password-derived token (`t` / `password_token` in the JSON response, today handed out only after a correct password) must never be sent to a visitor. That token grants full access.
- **A store that is protected today opens up.** Existing password-protected stores must behave exactly as before (Closed) until the owner changes them.
- **A visitor upload in Drop box overwrites or destroys an existing file**, including through a crafted request or two uploads racing for the same name.
- **Visual clutter** in the visitor view: no banners. The user rejected a banner in the mockups.

## Decisions made with the user

- **Password dialog: compact switch (mockup option B).** Below the password field: a label "Visitors without password", a three-way segmented control `Closed | Read-only | Drop box`, and one line of hint text explaining the selected option. Buttons stay as today: Remove / Cancel / Save (today's "Set" button becomes "Save").
- **Visitor view header:** in the header's control cell, the view-mode switch (Simple/Editor/Reader) and the password button are replaced by:
  - a lock icon (`#i-lock`, in the signal color) with a tooltip: Read-only: "Read-only. You can view and download, but not change anything." Drop box: "Drop box. You can view, download and add files, but not change or delete what's here."
  - an **Unlock to edit** button that asks for the password (reuse the existing unlock form and `verify-password` flow, in a dialog or inline), then switches to the full owner view without a page reload.
  - the theme button stays.
- **Visitor view body:** title and text are read-only; no delete buttons on files (tiles or list view); the "Add or drop files" tile, drag-and-drop and paste-to-upload are present in Drop box only.
- **Name collisions:** visitor uploads get a numbered copy; owner uploads keep replacing.
- **Classic page stays locked for visitors.** `item_detail_classic.html` (served for hosts not in `NEW_DESIGN_HOSTS`) does not get a visitor view. On those hosts the server treats Read-only and Drop box stores like Closed for visitors: the store JSON and every protected endpoint answer 403 `locked`, as today. Owners on the classic page work as before.
- **No token in store text (agreed after review).** Pasted image links must stop carrying the owner token (see "Token in store text" below). Links that already contain the token keep working; the token is not rotated.

## Token in store text

Today, pasting an image into a protected store writes a markdown image link with `?t=<password_token>` into the text (`item_detail.html` around line 1350, `item_detail_classic.html` around line 870). Sending that text to visitors would leak owner access. The fix:

- The new page stops adding `?t=` when inserting a pasted image's link; it stores the plain download URL.
- When the owner's page renders markdown, it adds the token to image and link URLs that point at this store's own downloads (so inline images still load in a Closed store, where an `<img>` can't send the password header).
- Before sending text to a visitor, the server removes every `t=<this store's token>` query parameter from it. This covers text written before the change. The stored text is not rewritten. (*My call:* no data migration; stripping on the way out is enough and leaves the owner's text untouched.)
- The classic page keeps adding `?t=` for owners. That's harmless, because its visitors only ever get 403, and the server strips the token from text for visitors on the new page anyway.

## Password dialog behavior

*My calls, from the review:*
- The `Closed | Read-only | Drop box` switch is always shown, but disabled until the store has a password or one has been typed into the field.
- New password: the switch starts at **Read-only**. Existing password: it shows the current setting.
- **Save** with an empty password field and an existing password keeps the password and updates only `visitor_access`. Save with a typed password sets the new password and the access setting. Only **Remove** removes the password. (Today an empty field plus "Set" removes the password. That changes.)
- The POST can carry `visitor_access` on its own, without `password`.

## Constraints and current state

- Django app in `store/`. Protection lives in `check_item_password` (`store/views.py`), which every protected view calls as a yes/no gate. It accepts the password (header `X-Item-Password`, JSON body, or `?password=`) or the token `?t=`.
- `Item.mode` already exists and means the *view* mode (simple/editor/reader), so the new field needs a different name. *My call:* `Item.visitor_access`, a `CharField` with choices `closed` / `read` / `dropbox`, default `closed`. The migration leaves existing rows `closed`, which is the anti-goal above handled.
- Files are stored at `<item_guid>/<filename>` (`storage.key_for`), so same name = same file. Uploads are a three-step flow: `generate_upload_params` (checks access, returns a signed key) → `upload_receive` (writes the file, authorized by the signed key) → `upload` (creates or touches the `Upload` row). The decision about which name a visitor gets, and that it must not overwrite, has to survive this flow: the signed token can't be reused to write over a name that was free when it was issued and taken by the time the bytes arrive.
- The front end (`store/templates/store/item_detail.html`, Vue) stores the password per store in localStorage and appends `?t=` to download URLs when it has the token. Visitors in Read-only/Drop box must be able to download with no token.
- `verify_password` returns whether a password is correct; leave it as is.

## Server behavior (what each request does)

Owner = request passes the existing password check. Visitor = it doesn't.

| Request | Owner | Visitor, Closed | Visitor, Read-only | Visitor, Drop box |
|---|---|---|---|---|
| GET store JSON | full, incl. token | 403 `locked` (as today) | store data, **no token**, text with `t=` stripped, plus `visitor_access` and a flag saying this is the visitor view | same as Read-only |
| POST text / name / mode / password / visitor_access | allowed | 403 | 403 | 403 |
| Download a file | allowed | 403 | allowed | allowed |
| Delete a file | allowed | 403 | 403 | 403 |
| Upload (all three steps) | allowed, same name replaces | 403 | 403 | allowed, same name → numbered copy |

The Read-only and Drop box columns apply on hosts in `NEW_DESIGN_HOSTS` only; on other hosts, visitors get the Closed column. The owner sets `visitor_access` through the existing store POST, next to `password` or on its own. Removing the password leaves the store fully open as today; *my call:* the stored `visitor_access` value is kept, so it reappears if a password is set again. That makes no difference while there is no password.

## Left to the builder

- How to restructure `check_item_password` (e.g. into a function that returns owner / visitor-with-access-level), as long as every view in the table above follows it.
- How the numbered-copy name is chosen and kept collision-free across the upload flow (e.g. pick the free name in `generate_upload_params` and have `upload_receive` refuse to write over an existing file for a visitor token, picking the next free name or failing cleanly).
- Exact placement and styling of the lock icon, tooltip and Unlock button, following the existing design tokens and the mockups in `.superpowers/brainstorm/` (`access-modes-v2.html` for the visitor view, option B in `access-modes.html` for the dialog).
- The hint text under the switch for Closed (*suggestion:* "Only people with the password can see this store.").
- Tests: follow the existing Django test files (`store/tests.py`, `store/test_uploads.py`). Cover every cell of the server table, the token never reaching a visitor (including through `t=` in old text), the classic host keeping visitors locked, the migration default, numbered copies, and Save with an empty field keeping the password.
- How paste-to-upload is wired now that the text box can be read-only (visitors can't paste into the text; Drop box visitors upload through the tile and drag-and-drop).
