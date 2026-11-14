"""Tests run on real CPSC records (tests/fixtures/recalls.json), not invented ones."""
import json
import os

import pytest

from caveat import cpsc, ledger, match, text
from caveat.store import open_store

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "recalls.json")


@pytest.fixture(scope="module")
def records():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def store(tmp_path, records):
    path = str(tmp_path / "t.rl")
    with open_store(path) as conn:
        cpsc.load(conn, records)
    return path


def test_company_names_split_into_name_alias_location():
    c = text.split_company("Allstar Marketing Group, LLC, of Winston-Salem, North Carolina")
    assert c["name"] == "Allstar Marketing Group, LLC"
    assert c["location"] == "Winston-Salem, North Carolina"
    assert c["key"] == "allstar marketing group"
    d = text.split_company("Weifang Luzhan Trade Co., Ltd., dba Yeeluzan, of China")
    assert d["alias"] == "Yeeluzan" and d["location"] == "China"


def test_company_key_ignores_case_punctuation_and_legal_suffix():
    assert text.company_key("Fisher-Price, Inc.") == text.company_key("FISHER PRICE")
    assert text.company_key("The Step2 Company, LLC") == "step2"


def test_title_firm_is_empty_when_the_title_names_a_product():
    assert text.title_firm("Eaton Recalls Electrical Meter Breakers Due to Shock Hazard") == "Eaton"
    assert text.title_firm("Rope Lights Recalled by CO2UCH") == "CO2UCH"
    assert text.title_firm("MJKSARE High Chairs Recalled Due to Entrapment and Fall Hazards") == ""


def test_hazards_come_from_the_title():
    assert text.hazards("X Recalls Y Due to Fire and Burn Hazards") == ["fire", "burn"]
    assert "fall" in text.hazards("X Recalls Ladders Due to Fall Hazard")


def test_units_and_retailers():
    assert text.units("About 265,000") == 265000
    assert text.units("About 4.7 million") == 4700000
    assert text.units("") is None
    assert text.retailers("Wal-Mart, Sam's Club, and Target stores nationwide") == ["Walmart", "Sam's Club", "Target"]


def test_loading_twice_changes_nothing(store, records):
    with open_store(store) as conn:
        before = conn.execute("MATCH (n) RETURN count(n) AS n").single()["n"]
        again = cpsc.load(conn, records)
        after = conn.execute("MATCH (n) RETURN count(n) AS n").single()["n"]
    assert again["new"] == [] and again["changed"] == [] and before == after


def test_brand_and_product_words_make_a_likely_match(store):
    with open_store(store, read_only=True) as conn:
        res = match.screen(conn, "Rock 'n Play Sleeper", brand="Fisher-Price")
    assert res["matches"] and res["matches"][0]["tier"] == "LIKELY"
    assert "Rock" in res["matches"][0]["recall"]["title"]


def test_same_product_from_another_firm_is_only_similar(store):
    with open_store(store, read_only=True) as conn:
        res = match.screen(conn, "tabletop fire pit", brand="Sharper Image", limit=10)
    tiers = {m["recall"]["title"].split(" Recalls ")[0]: m["tier"] for m in res["matches"]}
    assert tiers.get("Allstar") == "LIKELY"
    assert tiers.get("Southern Telecom") == "SIMILAR"


def test_generic_brand_match_is_not_promoted_to_likely(store):
    with open_store(store, read_only=True) as conn:
        res = match.screen(conn, "wooden high chair", brand="Stokke")
    assert [m["tier"] for m in res["matches"]] == ["POSSIBLE"]


def test_unrelated_product_finds_nothing(store):
    with open_store(store, read_only=True) as conn:
        res = match.screen(conn, "stainless steel water bottle", brand="Hydro Flask")
    assert res["matches"] == []


def test_a_recall_published_after_a_recommendation_raises_an_alert(tmp_path, records):
    path = str(tmp_path / "replay.rl")
    early = [r for r in records if (r.get("LastPublishDate") or "")[:10] <= "2026-11-01"]
    with open_store(path) as conn:
        cpsc.load(conn, early)
        rec = ledger.record(conn, "Sharper Image tabletop fire pit", brand="Sharper Image", agent="test")
        assert rec["status"] == "CLEAR"
        assert ledger.refresh_alerts(conn) == []
    with open_store(path) as conn:
        cpsc.load(conn, records)
        created = ledger.refresh_alerts(conn)
        assert len(created) == 1 and created[0]["tier"] == "LIKELY"
        assert ledger.refresh_alerts(conn) == []          # an alert is raised once
        alerts = ledger.alerts(conn)
    assert alerts[0]["decisions"][0]["id"] == rec["decision"]
    assert "Sharper Image" in alerts[0]["title"]
