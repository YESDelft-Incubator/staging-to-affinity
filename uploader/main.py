"""staging-to-affinity: uploads approved rows from Google staging sheets to Affinity.

Run:  python -m uploader.main            (all sheets in config/sheets.yaml)
      python -m uploader.main <sheet_id> (one sheet)

Each staging sheet has three tabs:
  Entries  rows to upload; the uploader writes status, affinity_person_id,
           processed_at and message back into it
  Config   key/value settings (target list, dry_run, ...)
  Mapping  which sheet column goes to which Affinity field (see docs/staging_sheet.md)

Version 1 supports person lists (MYCF). Company lists (scouting) come next.
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import yaml
from gspread.utils import rowcol_to_a1

from .affinity import Affinity, AffinityError
from .changelog import ChangeLog
from . import values as V

AMS = ZoneInfo("Europe/Amsterdam")
RESULT_COLUMNS = ["status", "affinity_person_id", "processed_at", "message"]
DEFAULT_MAX_ROWS = 100


# ---- reading the staging sheet ---------------------------------------------
def read_config(sh):
    rows = sh.worksheet("Config").get_all_values()
    return {r[0].strip(): (r[1].strip() if len(r) > 1 else "") for r in rows[1:] if r and r[0].strip()}


def read_mapping(sh):
    rows = sh.worksheet("Mapping").get_all_records()
    mapping = []
    for r in rows:
        col = V.clean_header(r.get("sheet_column"))
        if not col:
            continue
        mapping.append({
            "column": col,
            "field_id": str(r.get("affinity_field_id", "")).strip(),
            "type": str(r.get("field_type", "")).strip().lower(),
            "scope": str(r.get("scope", "")).strip().lower(),
            "mode": str(r.get("write_mode", "")).strip().lower() or "fill_if_empty",
            "label": str(r.get("label", "")).strip(),
        })
    return mapping


def truthy(v, default):
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("true", "yes", "1")


# ---- planning --------------------------------------------------------------
class RowPlan:
    """Everything the uploader intends to do for one sheet row."""

    def __init__(self):
        self.person_id = None
        self.create_person = None      # (first, last, email)
        self.list_entry_id = None
        self.create_list_entry = False
        self.writes = []               # planned field writes (dicts)
        self.note = None
        self.problems = []             # values that could not be converted
        self.messages = []


def convert(m, raw, fields):
    """Sheet value -> list of (affinity_value, display_text). Raises ValueError on problems."""
    t = m["type"]
    if t == "text":
        return [(raw.strip(), raw.strip())]
    if t == "country":
        name, ok = V.normalise_country(raw)
        if not ok:
            raise ValueError(f"country '{raw}' not recognised")
        return [(name, name)]
    if t == "date":
        iso = V.to_iso_date(raw)
        if not iso:
            raise ValueError(f"date '{raw}' not readable")
        return [(iso, iso)]
    options = fields.get(m["field_id"], {}).get("options", [])
    if t == "dropdown":
        o = V.match_dropdown(raw, options)
        if not o:
            raise ValueError(f"'{raw}' is not an option of {m['field_id']}")
        return [(o["id"], o["text"])]
    if t == "boolean_dropdown":
        o = V.match_boolean_dropdown(raw, options)
        if not o:
            raise ValueError(f"'{raw}' has no matching option in {m['field_id']}")
        return [(o["id"], o["text"])]
    if t == "multi_dropdown":
        matched, unknown = V.match_multi_dropdown(raw, options)
        if unknown:
            raise ValueError(f"unknown option(s) for {m['field_id']}: {', '.join(unknown)}")
        return [(o["id"], o["text"]) for o in matched]
    raise ValueError(f"unsupported field_type '{t}' for column {m['column']}")


def existing_text(fv):
    v = fv.get("value")
    return v.get("text", "") if isinstance(v, dict) else ("" if v is None else str(v))


def plan_row(row, cfg, mapping, fields, aff):
    plan = RowPlan()
    approved = V.is_approved(row)
    list_id = int(cfg["affinity_list_id"])

    email = (row.get("email") or "").strip().lower()
    if not email:
        raise ValueError("no email in row")

    # 1. convert sheet values; collect problems instead of failing on the first one
    converted, note_parts = [], []
    for m in mapping:
        raw = (row.get(m["column"]) or "").strip()
        if not raw:
            continue
        if m["type"] == "note":
            note_parts.append(f"{m['label'] or m['column']}:\n{raw}")
            continue
        try:
            converted.append((m, convert(m, raw, fields)))
        except ValueError as e:
            plan.problems.append(str(e))
    if plan.problems and not approved:
        return plan  # caller sets status 'review'; nothing is written
    if plan.problems:
        plan.messages.append("approved with skipped values: " + "; ".join(plan.problems))

    # 2. person: match on email only
    matches = aff.find_persons_by_email(email)
    if len(matches) > 1:
        raise ValueError(f"{len(matches)} persons in Affinity share {email}: "
                         + ", ".join(str(p["id"]) for p in matches))
    existing_global, existing_list = {}, {}
    if matches:
        plan.person_id = matches[0]["id"]
        person = aff.get_person(plan.person_id)
        for le in person.get("list_entries", []) or []:
            if int(le.get("list_id", 0)) == list_id:
                plan.list_entry_id = le["id"]
        for fv in aff.field_values(person_id=plan.person_id) or []:
            key = f"field-{fv['field_id']}"
            if fv.get("list_entry_id") is None:
                existing_global.setdefault(key, []).append(fv)
            elif plan.list_entry_id and fv.get("list_entry_id") == plan.list_entry_id:
                existing_list.setdefault(key, []).append(fv)
        plan.messages.append(f"existing person {plan.person_id}")
    else:
        plan.create_person = ((row.get("first_name") or "").strip(),
                              (row.get("last_name") or "").strip(), email)
    if not plan.list_entry_id:
        plan.create_list_entry = True
    else:
        plan.messages.append("already on list: only empty fields filled, edition appended")

    # 3. status on new list entries only
    if plan.create_list_entry and cfg.get("affinity_status_field_id"):
        sf = cfg["affinity_status_field_id"]
        o = V.match_dropdown(cfg.get("affinity_status_value", "New"), fields.get(sf, {}).get("options", []))
        if not o:
            raise ValueError(f"status '{cfg.get('affinity_status_value')}' not found on {sf}")
        plan.writes.append({"field_id": sf, "scope": "list", "op": "add", "value": o["id"],
                            "display": o["text"], "old": "(empty)"})

    # 4. field writes
    for m, vals in converted:
        current = (existing_global if m["scope"] == "global" else existing_list).get(m["field_id"], [])
        if m["mode"] == "append_text" and vals:
            if current:
                new = V.append_text(existing_text(current[0]), vals[0][1])
                if new != existing_text(current[0]):
                    plan.writes.append({"field_id": m["field_id"], "scope": m["scope"], "op": "update",
                                        "fv_id": current[0]["id"], "value": new, "display": new,
                                        "old": existing_text(current[0])})
            else:
                plan.writes.append({"field_id": m["field_id"], "scope": m["scope"], "op": "add",
                                    "value": vals[0][0], "display": vals[0][1], "old": "(empty)"})
        elif m["mode"] == "set" and current and len(vals) == 1:
            if existing_text(current[0]) != str(vals[0][1]):
                plan.writes.append({"field_id": m["field_id"], "scope": m["scope"], "op": "update",
                                    "fv_id": current[0]["id"], "value": vals[0][0],
                                    "display": vals[0][1], "old": existing_text(current[0])})
        elif current:  # fill_if_empty, or 'set' on multi-values: never overwrite
            continue
        else:
            for value, display in vals:
                plan.writes.append({"field_id": m["field_id"], "scope": m["scope"], "op": "add",
                                    "value": value, "display": display, "old": "(empty)"})

    if note_parts:
        edition = (row.get("mycf_edition") or "").strip()
        header = f"{cfg.get('note_title', 'Registration')} {edition}".strip()
        plan.note = header + "\n\n" + "\n\n".join(note_parts)
    return plan


def describe(plan, fields):
    parts = []
    if plan.create_person:
        parts.append(f"create person {plan.create_person[0]} {plan.create_person[1]}")
    if plan.create_list_entry:
        parts.append("add to list")
    for w in plan.writes:
        name = fields.get(w["field_id"], {}).get("name", w["field_id"])
        parts.append(f"{w['op']} {name}='{V.truncate(w['display'], 40)}'")
    if plan.note:
        parts.append("add note")
    return "; ".join(parts) or "nothing to change"


# ---- executing (live mode) ---------------------------------------------------
def execute(plan, row, cfg, fields, aff, log):
    list_id = int(cfg["affinity_list_id"])
    name = f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
    reason = cfg.get("log_reason", "Upload from staging sheet")

    def logged(entries, fn):
        """Log rows first; if that fails, nothing is written to Affinity."""
        batch = log.new_batch()
        first = log.append(batch, entries)
        results = []
        try:
            out = fn(results)
        finally:
            results += [("failed", "not attempted after earlier failure")] * (len(entries) - len(results))
            log.set_results(first, results)
        return first, out

    if plan.create_person:
        first, last, email = plan.create_person

        def do(results):
            p = aff.create_person(first, last, email)
            results.append(("success", ""))
            return p
        row_no, p = logged([{"tool": "api: POST /persons", "action": "create", "entity_type": "person",
                             "entity_name": name, "old_value": "(none)", "new_value": f"{name} <{email}>",
                             "reason": reason}], do)
        plan.person_id = p["id"]
        log.set_entity_id(row_no, plan.person_id)

    if plan.create_list_entry:
        def do(results):
            le = aff.add_list_entry(list_id, plan.person_id)
            results.append(("success", ""))
            return le
        row_no, le = logged([{"tool": f"api: POST /lists/{list_id}/list-entries", "action": "create",
                              "entity_type": "list_entry", "entity_id": plan.person_id, "entity_name": name,
                              "list_id": list_id, "old_value": "(none)",
                              "new_value": f"{name} ({plan.person_id})", "reason": reason}], do)
        plan.list_entry_id = le["id"]

    entries = []
    for w in plan.writes:
        entries.append({"tool": "api: PUT /field-values" if w["op"] == "update" else "api: POST /field-values",
                        "action": w["op"], "entity_type": "person", "entity_id": plan.person_id,
                        "entity_name": name, "list_id": list_id if w["scope"] == "list" else "",
                        "list_entry_id": plan.list_entry_id if w["scope"] == "list" else "",
                        "field_id": w["field_id"], "field_name": fields.get(w["field_id"], {}).get("name", ""),
                        "old_value": w["old"], "new_value": str(w["display"]), "reason": reason})
    if plan.note:
        entries.append({"tool": "api: POST /notes", "action": "create", "entity_type": "note",
                        "entity_name": name, "old_value": "(none)", "new_value": plan.note,
                        "reason": reason})
    if not entries:
        return

    def do(results):
        for w in plan.writes:
            try:
                if w["op"] == "update":
                    aff.update_field_value(w["fv_id"], w["value"])
                else:
                    aff.add_field_value(w["field_id"], plan.person_id, w["value"],
                                        plan.list_entry_id if w["scope"] == "list" else None)
                results.append(("success", ""))
            except AffinityError as e:
                results.append(("failed", str(e)[:300]))
        if plan.note:
            try:
                aff.add_note(plan.person_id, plan.note)
                results.append(("success", ""))
            except AffinityError as e:
                results.append(("failed", str(e)[:300]))
        failed = [r for r in results if r[0] == "failed"]
        if failed:
            raise AffinityError(f"{len(failed)} of {len(results)} writes failed: {failed[0][1]}")
    logged(entries, do)


# ---- one staging sheet -------------------------------------------------------
def run_sheet(gc, aff, sheet_id):
    sh = gc.open_by_key(sheet_id)
    cfg = read_config(sh)
    mapping = read_mapping(sh)
    dry_run = truthy(cfg.get("dry_run"), default=True)
    max_rows = int(cfg.get("max_rows_per_run") or DEFAULT_MAX_ROWS)
    if cfg.get("entity_type", "person") != "person":
        print(f"[{sh.title}] entity_type '{cfg.get('entity_type')}' not supported yet; skipped")
        return

    fields = {}
    for f in (aff.list_fields(int(cfg["affinity_list_id"])) or []) + (aff.person_fields() or []):
        fields[f"field-{f['id']}"] = {"name": f["name"], "options": f.get("dropdown_options") or []}

    ws = sh.worksheet("Entries")
    data = ws.get_all_values()
    header = [V.clean_header(h) for h in data[0]]
    missing = [c for c in RESULT_COLUMNS + ["email", "flags", "decision"] if c not in header]
    if missing:
        raise SystemExit(f"[{sh.title}] Entries is missing columns: {missing}")
    col = {h: i + 1 for i, h in enumerate(header)}

    log = None
    if not dry_run:
        me = aff.whoami()
        log = ChangeLog(gc, me["user"]["id"], me["user"]["email"],
                        cfg.get("log_context_note", sh.title))

    done = 0
    for i, values in enumerate(data[1:], start=2):
        row = {h: (values[j] if j < len(values) else "") for j, h in enumerate(header)}
        if not V.row_is_eligible(row):
            continue
        if done >= max_rows:
            print(f"[{sh.title}] max_rows_per_run={max_rows} reached; rest waits for next run")
            break
        done += 1
        now = datetime.now(AMS).strftime("%Y-%m-%d %H:%M")
        status, pid, msg = row.get("status"), row.get("affinity_person_id", ""), ""
        try:
            plan = plan_row(row, cfg, mapping, fields, aff)
            if plan.problems and not V.is_approved(row):
                msg = "needs decision=approve: " + "; ".join(plan.problems)
                if dry_run:
                    msg = "DRY RUN would set review: " + msg
                else:
                    status = "review"
            elif dry_run:
                msg = "DRY RUN would: " + describe(plan, fields)
                if plan.messages:
                    msg += " | " + " | ".join(plan.messages)
            else:
                execute(plan, row, cfg, fields, aff, log)
                status, pid = "uploaded", str(plan.person_id)
                msg = describe(plan, fields) + (" | " + " | ".join(plan.messages) if plan.messages else "")
        except Exception as e:  # one bad row must not stop the run
            status, msg = ("error", str(e)[:500]) if not dry_run else (status, f"DRY RUN error: {e}"[:500])
        ws.batch_update([
            {"range": rowcol_to_a1(i, col["status"]), "values": [[status]]},
            {"range": rowcol_to_a1(i, col["affinity_person_id"]), "values": [[pid]]},
            {"range": rowcol_to_a1(i, col["processed_at"]), "values": [[now]]},
            {"range": rowcol_to_a1(i, col["message"]), "values": [[msg]]},
        ], value_input_option="RAW")
        print(f"[{sh.title}] row {i}: {status} {msg[:120]}")
    print(f"[{sh.title}] {'dry run' if dry_run else 'live'}: {done} row(s) processed")


def main():
    gc = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    aff = Affinity(os.environ["AFFINITY_API_TOKEN"])
    if len(sys.argv) > 1:
        sheet_ids = sys.argv[1:]
    else:
        with open(os.path.join(os.path.dirname(__file__), "..", "config", "sheets.yaml")) as f:
            sheet_ids = [s["id"] for s in yaml.safe_load(f)["sheets"] if s.get("enabled", True)]
    for sid in sheet_ids:
        run_sheet(gc, aff, sid)


if __name__ == "__main__":
    main()
