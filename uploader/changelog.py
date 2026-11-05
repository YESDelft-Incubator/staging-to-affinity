"""Writes rows to the shared "Claude Affinity action log" (tab Log, columns A:V),
following the affinity-change-log skill: log rows first, then the Affinity write,
then fill result/error. If the log append fails, the Affinity write must not happen.
"""
from datetime import datetime, timezone

from .values import truncate

LOG_SHEET_ID = "1zuC8m0ZKNDJ_PrzJg-fmsv1Uh6p3F8X6Qa_yl6aiuyY"
LOG_TAB = "Log"
CONTEXT = "staging-to-affinity uploader"
APPROVAL = "pre-approved skill run"


class ChangeLog:
    def __init__(self, gc, user_id, user_email, context_note):
        self.ws = gc.open_by_key(LOG_SHEET_ID).worksheet(LOG_TAB)
        self.user_id, self.user_email = str(user_id), str(user_email)
        self.context_note = context_note
        self._seq = {}

    def new_batch(self):
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    def append(self, batch_id, entries):
        """entries: list of dicts with keys tool, action, entity_type, entity_id,
        entity_name, list_id, list_entry_id, field_id, field_name, old_value,
        new_value, reason. Returns the first sheet row number written."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = []
        for e in entries:
            n = self._seq.get(batch_id, 0) + 1
            self._seq[batch_id] = n
            rows.append([
                f"{batch_id}-{n:02d}", batch_id, now, self.user_id, self.user_email,
                CONTEXT, self.context_note, APPROVAL, e.get("tool", ""), e.get("action", ""),
                e.get("entity_type", ""), str(e.get("entity_id", "")), e.get("entity_name", ""),
                str(e.get("list_id", "")), str(e.get("list_entry_id", "")),
                e.get("field_id", ""), e.get("field_name", ""),
                truncate(e.get("old_value", "(empty)")), truncate(e.get("new_value", "")),
                "", "", e.get("reason", ""),
            ])
        res = self.ws.append_rows(rows, value_input_option="RAW", table_range="A1")
        rng = res["updates"]["updatedRange"]  # e.g. Log!A120:V122
        return int(rng.split("!")[1].split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))

    def set_results(self, first_row, results):
        """results: list of (result, error) in the same order as the appended rows."""
        values = [[r, e] for r, e in results]
        self.ws.update(values=values, range_name=f"T{first_row}:U{first_row + len(values) - 1}")

    def set_entity_id(self, row, entity_id):
        """Creates: the entity_id is only known after the Affinity call."""
        self.ws.update(values=[[str(entity_id)]], range_name=f"L{row}")
