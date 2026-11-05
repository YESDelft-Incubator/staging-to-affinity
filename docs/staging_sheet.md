# Staging sheet format

A staging sheet is a Google Sheet with the tabs Entries, Config, Mapping and optionally Aliases.
The uploader only reads `Config`, `Mapping` and `Aliases`, and only writes four columns in `Entries`.

## Entries

One row per record to upload. Columns are found by header name, so their order does not matter.

Required: `email`, `first_name`, `last_name`, `flags`, `decision`, `status`,
`affinity_person_id`, `processed_at`, `message`.

**Which rows are uploaded**

| status | flags | decision | result |
|---|---|---|---|
| new | empty | anything | uploaded |
| new or review | anything | approve | uploaded |
| anything else | | | left alone |

**What the uploader writes back**

| status | meaning |
|---|---|
| uploaded | done; `affinity_person_id` holds the Affinity ID |
| review | a value could not be converted (unknown dropdown option, unreadable date, country or phone). Check `message`, then set `decision` to `approve` to upload without those values, or add an alias |
| error | something failed (e.g. two Affinity persons share the email). Fix the cause, set `status` back to `new` to retry |

In dry-run mode `status` is not changed; `message` starts with `DRY RUN would:` and lists the planned changes.

## Config (key / value)

| key | example | meaning |
|---|---|---|
| affinity_list_id | 373806 | target list |
| entity_type | person | only `person` is supported in v1 |
| affinity_status_field_id | field-5966848 | Status field of the list |
| affinity_status_value | New | Status set on new list entries |
| dry_run | TRUE | TRUE = only report, write nothing to Affinity. Default TRUE when missing |
| max_rows_per_run | 100 | rows processed per run; the rest waits |
| note_title | MYCF registration | first line of the note, followed by the edition |
| log_reason | MYCF registration upload | `reason` column in the Claude Affinity action log |
| log_context_note | MYCF New Entries | `context_note` column in the Claude Affinity action log |

## Mapping

One row per sheet column that goes to Affinity. Adding a field = adding a row.

| column | values |
|---|---|
| sheet_column | header in Entries (case-insensitive) |
| affinity_field_id | e.g. `field-5991544`; empty for `note` |
| field_type | `text`, `phone`, `date`, `country`, `dropdown`, `multi_dropdown`, `boolean_dropdown`, `note` |
| scope | `list` (field on the list entry) or `global` (field on the person) |
| write_mode | `fill_if_empty` (never overwrite), `set` (overwrite on a repeat registration), `append_text` (add to existing text, e.g. editions), `note` |
| label | heading used inside the note (only for `note` rows) |

Dropdown values must match existing options exactly (case-insensitive). The uploader never creates new dropdown options.

`phone` writes international format: `+` and digits. 00 becomes +, a leading 0 is Dutch (+31),
9 digits starting with 6 is a Dutch mobile, other digit strings get a `+`.

**Repeat registration** (person already on the list): no new list entry, Status untouched,
`append_text` and `set` fields updated, all other fields only filled when empty.
**Existing person not yet on the list**: added to the list; global fields only filled when empty.

## Aliases (optional)

For sheet values that differ from the Affinity option, such as a typo in the Typeform option.
Applied before dropdown matching. Fixing a new spelling difference = adding a row.

| column | values |
|---|---|
| sheet_value | text as it appears in the sheet |
| affinity_option | exact Affinity option text |
| field_id | limit to one field, e.g. `field-5991544`; empty = all dropdown fields |
