# staging-to-affinity

Uploads approved rows from Google "staging sheets" to Affinity, once a day.
Used for MYCF registrations (list 373806); the scouting prospects sheet follows.

Upstream, a scheduled Claude run (skill `registration-preprocessing`) cleans new
registrations into the staging sheet. This repo is the only step that writes to Affinity.

## Safety rules built in

- Only rows that pass the upload rule (see `docs/staging_sheet.md`) are touched.
- Persons are matched on email. Two Affinity persons with the same email = error, nothing written.
- Existing values are never overwritten, except fields marked `set` or `append_text` in the Mapping tab.
- Dropdown values that don't match an existing option send the row to `review`; no new options are created.
- Every Affinity write gets a row in the "Claude Affinity action log" sheet **before** it is made.
  If that log write fails, nothing is written to Affinity.
- `dry_run` is on by default for every sheet (Config tab).

## How to

**Run now:** Actions tab -> "Staging sheets to Affinity" -> Run workflow (optionally one sheet ID).

**Add a staging sheet:** give it the tabs Entries, Config, Mapping (`docs/staging_sheet.md`),
share it with the service account as Editor, add it to `config/sheets.yaml`, start with `dry_run` TRUE.

**Add a field:** add a row to the sheet's Mapping tab. No code change.

**Retry a failed row:** read `message`, fix the cause, set `status` back to `new`.

## Setup (once)

Repository secrets (Settings -> Secrets and variables -> Actions):

| secret | value |
|---|---|
| AFFINITY_API_TOKEN | Affinity API key |
| GOOGLE_SERVICE_ACCOUNT_JSON | full JSON key of a Google service account |

The service account needs Editor access to every staging sheet and to the
"Claude Affinity action log" sheet (ID in `uploader/changelog.py`).

## Code

| file | what |
|---|---|
| `uploader/main.py` | reads the sheet, plans each row, writes to Affinity, writes back status |
| `uploader/values.py` | upload rule and value conversion (dropdowns, countries, dates) |
| `uploader/affinity.py` | Affinity API v1 calls |
| `uploader/changelog.py` | rows for the Claude Affinity action log |
| `tests/` | run with `python -m pytest tests`; the workflow runs them before every upload |
