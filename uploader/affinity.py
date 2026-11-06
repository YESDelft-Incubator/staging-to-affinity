"""Minimal Affinity API v1 client used by the uploader.

Only the calls the uploader needs. Write methods are called only after the
change log row is written (see changelog.py and main.execute).
"""
import time

import requests

BASE = "https://api.affinity.co"


class AffinityError(Exception):
    pass


class Affinity:
    def __init__(self, token, timeout=30):
        self.s = requests.Session()
        self.s.auth = ("", token)  # v1 uses basic auth with an empty username
        self.timeout = timeout

    def _call(self, method, path, **kw):
        for attempt in range(5):
            r = self.s.request(method, BASE + path, timeout=self.timeout, **kw)
            if r.status_code == 429:  # rate limited: wait and retry
                time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            if r.status_code >= 400:
                raise AffinityError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
            return r.json() if r.text else None
        raise AffinityError(f"{method} {path} -> rate limited 5 times")

    # ---- reads -----------------------------------------------------------
    def whoami(self):
        return self._call("GET", "/auth/whoami")

    def list_fields(self, list_id):
        """Fields of a list, including dropdown options (v1 /fields)."""
        return self._call("GET", "/fields", params={"list_id": list_id})

    def person_fields(self):
        """Global person fields (v1 /persons/fields)."""
        return self._call("GET", "/persons/fields")

    def find_persons_by_email(self, email):
        """Persons whose emails contain this exact address (case-insensitive)."""
        res = self._call("GET", "/persons", params={"term": email})
        persons = res.get("persons", []) if isinstance(res, dict) else res
        email = email.lower()
        return [p for p in persons if email in [e.lower() for e in p.get("emails", [])]]

    def find_organizations(self, name):
        """Organizations matching a name search (v1 /organizations?term=)."""
        res = self._call("GET", "/organizations", params={"term": name})
        return res.get("organizations", []) if isinstance(res, dict) else (res or [])

    def get_person(self, person_id):
        return self._call("GET", f"/persons/{person_id}")

    def field_values(self, person_id=None, list_entry_id=None):
        params = {"person_id": person_id} if person_id else {"list_entry_id": list_entry_id}
        return self._call("GET", "/field-values", params=params)

    # ---- writes (call only after the log row is written) -----------------
    def create_person(self, first_name, last_name, email):
        return self._call("POST", "/persons", json={
            "first_name": first_name, "last_name": last_name or "", "emails": [email]})

    def add_list_entry(self, list_id, entity_id):
        return self._call("POST", f"/lists/{list_id}/list-entries", json={"entity_id": entity_id})

    def add_field_value(self, field_id, entity_id, value, list_entry_id=None):
        body = {"field_id": int(str(field_id).replace("field-", "")),
                "entity_id": entity_id, "value": value}
        if list_entry_id:
            body["list_entry_id"] = list_entry_id
        return self._call("POST", "/field-values", json=body)

    def update_field_value(self, field_value_id, value):
        return self._call("PUT", f"/field-values/{field_value_id}", json={"value": value})

    def add_note(self, person_id, content):
        return self._call("POST", "/notes", json={"person_ids": [person_id], "content": content})
