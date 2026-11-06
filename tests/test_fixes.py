"""Fixes after the first Talent dry run (Nov 2026)."""
from uploader import values as V
from uploader.main import plan_row

from test_uploader import FakeAffinity, T_CFG, T_FIELDS, T_MAPPING, T_ROW


def test_multi_dropdown_without_spaces_after_commas():
    opts = [{"id": 1, "text": "Full Time"}, {"id": 2, "text": "Part Time"}, {"id": 3, "text": "Other"},
            {"id": 4, "text": "Automotive, Transportation, and Logistics"}]
    matched, unknown = V.match_multi_dropdown("Full Time,Part Time,other", opts)
    assert sorted(o["id"] for o in matched) == [1, 2, 3] and unknown == []
    matched, unknown = V.match_multi_dropdown("Automotive,Transportation, and Logistics,Full Time", opts)
    assert sorted(o["id"] for o in matched) == [1, 4] and unknown == []


def test_person_created_earlier_in_run_is_reused():
    fake = FakeAffinity(person={"list_entries": []})      # search finds nothing (index lag)
    plan = plan_row(dict(T_ROW), T_CFG, T_MAPPING, T_FIELDS, fake, (), {"test.person@example.com": 123})
    assert plan.create_person is None and plan.person_id == 123 and plan.create_list_entry
