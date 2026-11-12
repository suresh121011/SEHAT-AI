"""Referral closure tracking (docs/21, architecture §16): the pure parts. Closed lists, the forward-only status
chain, the 48-hour overdue rule and the demo facility directory. Synthetic data only."""

from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest

from app.ai import guard
from app.referral_notes import options as o

T0 = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)


def test_every_closed_list_matches_its_request_type_and_has_plain_labels():
    pairs = [(o.REASONS, o.ReasonCode), (o.TRANSPORT, o.TransportMode), (o.INSURANCE, o.InsuranceScheme),
             (o.OUTCOMES, o.OutcomeCode), (o.BARRIERS, o.BarrierCode), (o.STATUS_LABELS, o.Status)]
    for labels, literal in pairs:
        assert set(labels) == set(get_args(literal))
        for code, label in labels.items():
            assert label.strip() and len(label) <= 80, code
            assert guard.check(label) is None, label  # no diagnostic or prescriptive wording in any option


def test_status_chain_only_moves_forward_and_ends_in_a_closed_state():
    assert o.TRANSITIONS["referred"] == ("in_transit", "reached", "not_reached")
    assert o.TRANSITIONS["in_transit"] == ("reached", "not_reached")
    assert o.TRANSITIONS["reached"] == ("seen",)
    assert o.TRANSITIONS["seen"] == ("outcome_recorded",)
    assert o.TRANSITIONS["outcome_recorded"] == () and o.TRANSITIONS["not_reached"] == ()
    assert o.TERMINAL == {"outcome_recorded", "not_reached"}
    assert set(o.TRANSITIONS) == set(get_args(o.Status))
    order = list(get_args(o.Status))
    for status, nexts in o.TRANSITIONS.items():  # never backwards, never to itself
        assert all(n != status and n != "referred" for n in nexts)
        if status != "not_reached":
            assert all(order.index(n) > order.index(status) for n in nexts if n != "not_reached")


@pytest.mark.parametrize("status,hours,expected", [
    ("referred", 47.99, False),
    ("referred", 48, True),
    ("in_transit", 72, True),
    ("in_transit", 1, False),
    ("reached", 500, False),  # the patient arrived: the closure gap is closed
    ("seen", 500, False),
    ("outcome_recorded", 500, False),
    ("not_reached", 500, False),
])
def test_overdue_after_48_hours_without_an_update_before_arrival(status, hours, expected):
    assert o.OVERDUE_HOURS == 48
    assert o.is_overdue(status, T0, T0 + timedelta(hours=hours)) is expected


def test_hours_since_update_rounds_down():
    assert o.hours_since(T0, T0 + timedelta(hours=52, minutes=59)) == 52
    assert o.hours_since(T0, T0) == 0


def test_facility_directory_codes_and_destinations():
    for code, f in o.FACILITIES.items():
        assert code == f.code and o.FACILITY_CODE.fullmatch(code)
        assert f.level in ("PHC", "CHC", "SDH", "DH", "MCH")
    destinations = [d["code"] for d in o.destinations()]
    assert destinations and all(o.FACILITIES[c].level != "PHC" for c in destinations)
    assert set(destinations) == set(get_args(o.DestinationCode))
    assert o.facility_view("PHC-KHURDA-01")["name"] == "Primary Health Centre, Khurda"
    unknown = o.facility_view("PHC-PURI-02")  # a code missing from the demo directory shows as the code itself
    assert unknown == {"code": "PHC-PURI-02", "name": "PHC-PURI-02", "level": None, "district": None}
