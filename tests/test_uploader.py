import pytest

from uploader import values as V
from uploader.main import plan_row

# ---- upload rule ----------------------------------------------------------
@pytest.mark.parametrize("status,flags,decision,expected", [
    ("new", "", "", True),
    ("new", "duplicate_email", "", False),
    ("new", "duplicate_email", "approve", True),
    ("review", "spam_suspect", "approve", True),
    ("review", "", "", False),
    ("skip", "", "approve", False),
    ("uploaded", "", "", False),
    ("error", "", "", False),
    ("New ", "", "", True),
    ("new", "", "Approve", True),
])
def test_row_is_eligible(status, flags, decision, expected):
    assert V.row_is_eligible({"status": status, "flags": flags, "decision": decision}) is expected


# ---- dropdowns -------------------------------------------------------------
OPTS = [{"id": 1, "text": "Automotive, Transportation, and Logistics"},
        {"id": 2, "text": "Energy and Utilities"},
        {"id": 3, "text": "Constuction, Real estate, and Civil infrastructure"},
        {"id": 4, "text": "Finance and Insurance"}]


def test_multi_dropdown_options_with_commas():
    matched, unknown = V.match_multi_dropdown(
        "Constuction, Real estate, and Civil infrastructure, Energy and Utilities", OPTS)
    assert [o["id"] for o in matched] == [3, 2] and unknown == []


def test_multi_dropdown_unknown_part():
    matched, unknown = V.match_multi_dropdown("Finance and Insurance, Space", OPTS)
    assert [o["id"] for o in matched] == [4] and unknown == ["Space"]


def test_boolean_dropdown():
    yn = [{"id": 10, "text": "Yes"}, {"id": 11, "text": "No"}]
    assert V.match_boolean_dropdown("TRUE", yn)["id"] == 10
    assert V.match_boolean_dropdown("FALSE", yn)["id"] == 11
    assert V.match_boolean_dropdown("maybe", yn) is None


def test_alias_fixes_typo_inside_multi_value():
    aliases = [{"sheet_value": "Constuction, Real estate, and Civil infrastructure",
                "affinity_option": "Construction, Real estate, and Civil infrastructure", "field_id": ""}]
    opts = OPTS + [{"id": 5, "text": "Construction, Real estate, and Civil infrastructure"}]
    raw = V.apply_aliases("Constuction, Real estate, and Civil infrastructure, Energy and Utilities",
                          aliases, "field-1")
    matched, unknown = V.match_multi_dropdown(raw, opts)
    assert sorted(o["id"] for o in matched) == [2, 5] and unknown == []


def test_alias_limited_to_field():
    aliases = [{"sheet_value": "Foo", "affinity_option": "Bar", "field_id": "field-2"}]
    assert V.apply_aliases("Foo", aliases, "field-1") == "Foo"
    assert V.apply_aliases("Foo", aliases, "field-2") == "Bar"


@pytest.mark.parametrize("raw,expected", [
    ("+31600000002", "+31600000002"), ("31600000003", "+31600000003"),
    ("66900000004", "+66900000004"), ("0612345678", "+31612345678"),
    ("0031612345678", "+31612345678"), ("612345678", "+31612345678"),
    ("+31 6 1234 5678", "+31612345678"), ("abc", None), ("123", None)])
def test_phone(raw, expected):
    assert V.normalise_phone(raw) == expected


# ---- other conversions ------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Nederland", "Netherlands"), ("the Netherlands", "Netherlands"), ("NL", "Netherlands"),
    ("Germany", "Germany"), ("Duitsland", "Germany"), ("België", "Belgium")])
def test_country(raw, expected):
    assert V.normalise_country(raw) == (expected, True)


def test_country_unknown_kept():
    assert V.normalise_country("Atlantis") == ("Atlantis", False)


def test_dates_and_append():
    assert V.to_iso_date("2026-09-18 17:01:41") == "2026-09-18T17:01:41"
    assert V.to_iso_date("2026-09-18") == "2026-09-18T00:00:00"
    assert V.to_iso_date("18/09/2026") is None
    assert V.append_text("December 2026", "March 2027") == "December 2026; March 2027"
    assert V.append_text("December 2026", "December 2026") == "December 2026"


# ---- planning against a fake Affinity -------------------------------------------
class FakeAffinity:
    def __init__(self, persons=None, person=None, fvs=None):
        self.persons, self.person, self.fvs = persons or [], person or {}, fvs or []

    def find_persons_by_email(self, email):
        return self.persons

    def get_person(self, pid):
        return self.person

    def field_values(self, person_id=None, list_entry_id=None):
        return self.fvs

    def find_organizations(self, name):
        return getattr(self, "orgs", [])


CFG = {"affinity_list_id": "373806", "affinity_status_field_id": "field-5966848",
       "affinity_status_value": "New", "note_title": "MYCF registration"}
FIELDS = {"field-5966848": {"name": "Status", "options": [{"id": 7, "text": "New"}]},
          "field-5991544": {"name": "Focus areas", "options": OPTS},
          "field-5323063": {"name": "Phone number Y!D", "options": []},
          "field-5991539": {"name": "MYCF editions registered", "options": []},
          "field-5991541": {"name": "Country", "options": []}}
MAPPING = [
    {"column": "focus_areas", "field_id": "field-5991544", "type": "multi_dropdown", "scope": "list", "mode": "fill_if_empty", "label": ""},
    {"column": "phone", "field_id": "field-5323063", "type": "text", "scope": "global", "mode": "fill_if_empty", "label": ""},
    {"column": "mycf_edition", "field_id": "field-5991539", "type": "text", "scope": "list", "mode": "append_text", "label": ""},
    {"column": "country", "field_id": "field-5991541", "type": "country", "scope": "list", "mode": "fill_if_empty", "label": ""},
    {"column": "background_description", "field_id": "", "type": "note", "scope": "", "mode": "note", "label": "Background description"},
]
ROW = {"email": "test.person@example.com", "first_name": "Test", "last_name": "Person",
       "status": "new", "flags": "", "decision": "", "mycf_edition": "December 2026",
       "phone": "+31600000001", "country": "Netherlands",
       "focus_areas": "Constuction, Real estate, and Civil infrastructure, Energy and Utilities",
       "background_description": "Founded two businesses."}


def test_plan_new_person():
    plan = plan_row(dict(ROW), CFG, MAPPING, FIELDS, FakeAffinity())
    assert plan.create_person == ("Test", "Person", "test.person@example.com")
    assert plan.create_list_entry
    ops = [(w["field_id"], w["op"], w["display"]) for w in plan.writes]
    assert ("field-5966848", "add", "New") in ops
    assert ("field-5991544", "add", "Energy and Utilities") in ops
    assert ("field-5323063", "add", "+31600000001") in ops
    assert plan.note.startswith("MYCF registration December 2026")


def test_plan_existing_person_on_list_only_fills_empty_and_appends():
    fake = FakeAffinity(
        persons=[{"id": 99, "emails": ["test.person@example.com"]}],
        person={"list_entries": [{"id": 555, "list_id": 373806}]},
        fvs=[{"id": 1, "field_id": 5323063, "list_entry_id": None, "value": "+31600000000"},
             {"id": 2, "field_id": 5991539, "list_entry_id": 555, "value": "September 2026"}])
    plan = plan_row(dict(ROW), CFG, MAPPING, FIELDS, fake)
    assert not plan.create_person and not plan.create_list_entry
    ops = {w["field_id"]: w for w in plan.writes}
    assert "field-5966848" not in ops                      # status untouched
    assert "field-5323063" not in ops                      # phone not overwritten
    assert ops["field-5991539"]["op"] == "update"
    assert ops["field-5991539"]["value"] == "September 2026; December 2026"


def test_plan_unknown_option_needs_approval():
    row = dict(ROW, focus_areas="Space")
    plan = plan_row(row, CFG, MAPPING, FIELDS, FakeAffinity())
    assert plan.problems and not plan.writes              # nothing planned
    plan = plan_row(dict(row, decision="approve"), CFG, MAPPING, FIELDS, FakeAffinity())
    assert plan.writes and "field-5991544" not in [w["field_id"] for w in plan.writes]


def test_plan_ambiguous_email_raises():
    fake = FakeAffinity(persons=[{"id": 1, "emails": []}, {"id": 2, "emails": []}])
    with pytest.raises(ValueError):
        plan_row(dict(ROW), CFG, MAPPING, FIELDS, fake)


# ---- Talent features: config values, add_missing, template, counter, company lookup ----
def test_ignore_flags():
    ign = ["invalid_phone", "linkedin_not_usable"]
    assert V.row_is_eligible({"status": "new", "flags": "invalid_phone", "decision": ""}, ign)
    assert not V.row_is_eligible({"status": "new", "flags": "invalid_phone, name_inferred", "decision": ""}, ign)
    assert not V.row_is_eligible({"status": "new", "flags": "invalid_phone", "decision": ""})


def test_template():
    t = "[{submitted_at|date}: ]{job_title}[ ({company})]"
    assert V.render_template(t, {"submitted_at": "2026-10-23 10:00:00", "job_title": "Process Engineer",
                                 "company": "AddOptics"}) == "2026-10-23: Process Engineer (AddOptics)"
    assert V.render_template(t, {"submitted_at": "2026-10-23", "job_title": "Process Engineer",
                                 "company": ""}) == "2026-10-23: Process Engineer"


def test_normalise_company():
    assert V.normalise_company("Villari B.V.") == V.normalise_company("Villari") == "villari"
    assert V.normalise_company("Effium BV") == "effium"


T_CFG = {"affinity_list_id": "376483", "affinity_status_field_id": "field-5991214",
         "affinity_status_value": "New", "application_type": "Direct application"}
T_FIELDS = {"field-5991214": {"name": "Status", "options": [{"id": 1, "text": "New"}]},
            "field-6000986": {"name": "Application type",
                              "options": [{"id": 41, "text": "Open application"},
                                          {"id": 42, "text": "Direct application"}]},
            "field-6000989": {"name": "Applications", "options": []},
            "field-6000990": {"name": "Number of applications", "options": []},
            "field-6000987": {"name": "Companies applied to", "options": []}}
T_MAPPING = [
    {"column": "config:application_type", "field_id": "field-6000986", "type": "multi_dropdown",
     "scope": "list", "mode": "add_missing", "label": ""},
    {"column": "job_title", "field_id": "field-6000989", "type": "template", "scope": "list",
     "mode": "append_text", "label": "[{submitted_at|date}: ]{job_title}[ ({company})]"},
    {"column": "job_title", "field_id": "field-6000990", "type": "counter", "scope": "list",
     "mode": "", "label": "field-6000989"},
    {"column": "company", "field_id": "field-6000987", "type": "company_lookup", "scope": "list",
     "mode": "add_missing", "label": ""},
]
T_ROW = {"email": "test.person@example.com", "first_name": "Test", "last_name": "Person",
         "status": "new", "flags": "", "decision": "", "submitted_at": "2026-11-04",
         "job_title": "Mechanical Engineer", "company": "CIWI"}


def test_talent_new_person():
    fake = FakeAffinity()
    fake.orgs = [{"id": 700, "name": "CIWI"}]
    plan = plan_row(dict(T_ROW), T_CFG, T_MAPPING, T_FIELDS, fake)
    ops = {(w["field_id"], w["op"], str(w["display"])) for w in plan.writes}
    assert ("field-6000986", "add", "Direct application") in ops
    assert ("field-6000989", "add", "2026-11-04: Mechanical Engineer (CIWI)") in ops
    assert ("field-6000990", "add", "1") in ops
    assert ("field-6000987", "add", "CIWI") in ops


def test_talent_second_application_existing_entry():
    fake = FakeAffinity(
        persons=[{"id": 9, "emails": ["test.person@example.com"]}],
        person={"list_entries": [{"id": 77, "list_id": 376483}]},
        fvs=[{"id": 1, "field_id": 6000986, "list_entry_id": 77, "value": {"id": 41, "text": "Open application"}},
             {"id": 2, "field_id": 6000989, "list_entry_id": 77, "value": "2026-10-01: Process Engineer (AddOptics)"},
             {"id": 3, "field_id": 6000990, "list_entry_id": 77, "value": 1},
             {"id": 4, "field_id": 6000987, "list_entry_id": 77, "value": 500}])
    fake.orgs = [{"id": 700, "name": "CIWI B.V."}]
    plan = plan_row(dict(T_ROW), T_CFG, T_MAPPING, T_FIELDS, fake)
    ops = {w["field_id"]: w for w in plan.writes}
    assert ops["field-6000986"]["value"] == 42                     # Direct added next to Open
    assert ops["field-6000989"]["value"] == ("2026-10-01: Process Engineer (AddOptics); "
                                              "2026-11-04: Mechanical Engineer (CIWI)")
    assert ops["field-6000990"]["op"] == "update" and ops["field-6000990"]["value"] == 2
    assert ops["field-6000987"]["value"] == 700                    # legal suffix ignored
    assert "field-5991214" not in ops                              # status untouched


def test_talent_retry_does_not_double_count():
    fake = FakeAffinity(
        persons=[{"id": 9, "emails": ["test.person@example.com"]}],
        person={"list_entries": [{"id": 77, "list_id": 376483}]},
        fvs=[{"id": 1, "field_id": 6000986, "list_entry_id": 77, "value": {"id": 42, "text": "Direct application"}},
             {"id": 2, "field_id": 6000989, "list_entry_id": 77, "value": "2026-11-04: Mechanical Engineer (CIWI)"},
             {"id": 3, "field_id": 6000990, "list_entry_id": 77, "value": 1},
             {"id": 4, "field_id": 6000987, "list_entry_id": 77, "value": 700}])
    fake.orgs = [{"id": 700, "name": "CIWI"}]
    plan = plan_row(dict(T_ROW), T_CFG, T_MAPPING, T_FIELDS, fake)
    assert plan.writes == []


def test_company_not_found_is_soft():
    fake = FakeAffinity()
    fake.orgs = []
    plan = plan_row(dict(T_ROW), T_CFG, T_MAPPING, T_FIELDS, fake)
    assert not plan.problems
    assert "field-6000987" not in [w["field_id"] for w in plan.writes]
    assert any("not found" in m for m in plan.messages)
