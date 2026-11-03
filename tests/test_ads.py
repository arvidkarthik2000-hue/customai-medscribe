"""Ad zone tests: firewall, caps, exclusivity, hides, hash chain, invalid traffic,
roll-up maths, share links and day-level exports. Offline; own throwaway database."""
from __future__ import annotations

import ast
import csv
import io
import json
import os
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ads-test-"))

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.ads import app as ads_module
from app.ads import db, events, metrics, serving

ADS_DIR = Path(__file__).resolve().parent.parent / "app" / "ads"


@pytest.fixture(scope="module")
def client():
    db.reset_for_tests(f"ads_test_{uuid.uuid4().hex[:8]}")
    c = TestClient(ads_module.app)
    c.cookies.set(auth.COOKIE, auth._serializer.dumps({"v": 1, "role": "network", "specialty": "family_medicine", "state": "NJ"}))
    c.get("/health")  # seeds
    yield c


@pytest.fixture()
def anon():
    return TestClient(ads_module.app)


def _serve(client, slot="drafting_wait", prescriber="DEMO-001", specialty="family_medicine", state="NJ", hour=10):
    return client.post("/serve", json={"slot": slot, "prescriber_id": prescriber, "specialty": specialty, "state": state, "hour": hour})


def _event(client, imp, etype, **kw):
    body = {"impression_id": imp, "type": etype, "visible": True, **kw}
    return client.post("/events", json=body, headers=kw.pop("headers", None) or {})


def _network_prescriber(specialty="family_medicine", state="NJ"):
    with db.tx() as conn:
        return conn.execute(
            "SELECT * FROM prescribers WHERE is_test=0 AND active=1 AND verified=1 AND specialty=? AND state=? ORDER BY id LIMIT 1",
            (specialty, state),
        ).fetchone()["id"]


# ---------------------------------------------------------------- firewall

def test_ad_zone_imports_nothing_clinical():
    for path in ADS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = ("." * node.level) + (node.module or "")
                assert "clinical" not in mod and "drugs" not in mod, f"{path.name} imports {mod}"
                names = [a.name for a in node.names]
                assert not ({"clinical", "drugs"} & set(names)), f"{path.name} imports {names}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    assert "clinical" not in a.name and "drugs" not in a.name, f"{path.name} imports {a.name}"


@pytest.mark.parametrize("extra", [
    {"patient_id": "p1"}, {"transcript": "patient has diabetes"}, {"note": {"plan": "start metformin"}},
    {"visit_id": "v_1"}, {"diagnosis": "E11.9"}, {"medications": ["warfarin"]},
])
def test_serve_rejects_any_extra_field(client, extra):
    body = {"slot": "drafting_wait", "prescriber_id": "DEMO-001", "specialty": "family_medicine", "state": "NJ", "hour": 9, **extra}
    r = client.post("/serve", json=body)
    assert r.status_code == 422


def test_events_reject_extra_fields(client):
    r = client.post("/events", json={"impression_id": "imp_" + "0" * 32, "type": "rendered", "patient_id": "p1"})
    assert r.status_code == 422


def test_serve_needs_session(anon):
    assert _serve(anon).status_code == 401


def test_unit_is_labelled_and_explained(client):
    r = _serve(client, slot="schedule_card")
    assert r.status_code == 200, r.text
    unit = r.json()["unit"]
    assert unit["sponsored_label"] == "Sponsored"
    assert unit["sample_label"] == "Sample ad, not a real product"
    assert "never based on anything said in a visit" in unit["why"]["text"].lower()
    assert unit["pi_path"].startswith("/pi/")
    assert "(fictional)" in unit["company"]
    pi = client.get(unit["pi_path"])
    assert pi.status_code == 200 and "not a real product" in pi.text


# ---------------------------------------------------------------- test traffic and invalid traffic

def test_demo_traffic_is_test_and_never_billed(client):
    before = client.get("/api/overview").json()
    r = _serve(client)
    assert r.status_code == 200
    imp = r.json()["impression_id"]
    assert _event(client, imp, "rendered").json()["traffic"] == "test"
    assert _event(client, imp, "viewable").json()["traffic"] == "test"
    after = client.get("/api/overview").json()
    assert after["totals"]["served"] == before["totals"]["served"]
    assert after["totals"]["viewable"] == before["totals"]["viewable"]
    assert after["totals"]["spend"] == before["totals"]["spend"]
    assert after["live_today"]["test"]["served"] == before["live_today"]["test"]["served"] + 1
    live = client.get("/api/live").json()
    assert live["items"][0]["traffic"] == "test" and live["items"][0]["prescriber"] == "Test account"


def test_invalid_traffic_rules(client):
    pid = _network_prescriber()
    seen = {}
    for reason, etype, kw in [
        ("background_tab", "rendered", {"visible": False}),
        ("automated_client", "rendered", {"headers": {"user-agent": "Mozilla/5.0 HeadlessChrome/120"}}),
        ("viewable_without_render", "viewable", {}),
    ]:
        r = _serve(client, prescriber=pid)
        assert r.status_code == 200, r.text
        imp = r.json()["impression_id"]
        headers = kw.pop("headers", {})
        body = {"impression_id": imp, "type": etype, "visible": kw.get("visible", True)}
        res = client.post("/events", json=body, headers=headers).json()
        seen[reason] = res
        assert res["traffic"] == "ivt" and res["reason"] == reason
    # duplicate render of a valid impression
    imp = _serve(client, prescriber=pid).json()["impression_id"]
    assert _event(client, imp, "rendered").json()["traffic"] == "valid"
    dup = _event(client, imp, "rendered").json()
    assert dup["traffic"] == "ivt" and dup["reason"] == "duplicate"


def test_unknown_impression_is_rejected(client):
    assert _event(client, "imp_" + uuid.uuid4().hex, "rendered").status_code == 404


# ---------------------------------------------------------------- caps, hides, boxed warnings, exclusivity

def test_frequency_cap_three_per_brand_per_week(client):
    client.post("/api/test/reset-caps")
    counts: dict[str, int] = {}
    for _ in range(80):
        r = _serve(client)
        if r.status_code == 204:
            break
        body = r.json()
        _event(client, body["impression_id"], "rendered")
        brand = body["unit"]["brand"]
        counts[brand] = counts.get(brand, 0) + 1
    assert counts, "nothing served"
    assert max(counts.values()) <= 3
    assert _serve(client).status_code == 204  # every eligible brand is capped
    client.post("/api/test/reset-caps")
    assert _serve(client).status_code == 200


def test_hide_suppresses_brand_for_30_days(client):
    client.post("/api/test/reset-caps")
    r = _serve(client, slot="schedule_card")
    body = r.json()
    hidden = body["unit"]["brand"]
    _event(client, body["impression_id"], "rendered")
    assert _event(client, body["impression_id"], "hide").status_code == 200
    for _ in range(25):
        r = _serve(client, slot="schedule_card")
        if r.status_code == 204:
            break
        assert r.json()["unit"]["brand"] != hidden
        _event(client, r.json()["impression_id"], "rendered")
    client.post("/api/test/reset-caps")


def test_boxed_warning_brands_never_get_reminder_cards(client):
    with db.tx() as conn:
        for b in conn.execute("SELECT * FROM brands WHERE boxed_warning=1").fetchall():
            for slot in ("schedule_card", "drafting_wait"):
                fmts = {c["format"] for c in serving.eligible_creatives(conn, b, slot, serving.today())}
                assert "reminder" not in fmts
                assert fmts, f"{b['name']} still needs a claim card for {slot}"


def test_one_brand_per_category_per_office(client):
    client.post("/api/test/reset-caps")
    with db.tx() as conn:
        conn.execute("INSERT INTO brands VALUES ('b-rival','Rivalto','rivastinex','Lipid management','Test Co',0,'#555555','dots','x','x','x')")
        conn.execute(
            "INSERT INTO creatives VALUES ('cr-rival-v2','b-rival',2,'claim',?, 'h','b','r','PI','MLR-T-1','2099-01-01','t','2026-01-01','approved','2026-01-01')",
            (json.dumps(["schedule_card", "drafting_wait"]),),
        )
        conn.execute(
            "INSERT INTO campaigns VALUES ('c-rival','b-rival','Rival','cpm',50,NULL,100000,NULL,?,?,?,?,?,3,1,'active','tok-rival',NULL,'pending_counsel','x','test')",
            ((serving.today()).isoformat(), "2099-01-01", json.dumps(["family_medicine"]), json.dumps(["NJ"]),
             json.dumps(["schedule_card", "drafting_wait"])),
        )
        conn.execute("DELETE FROM category_locks WHERE office_id=?", ("O-DEMO-001",))
    lipid = set()
    for _ in range(60):
        r = _serve(client)
        if r.status_code == 204:
            break
        u = r.json()["unit"]
        if u["category"] == "Lipid management":
            lipid.add(u["brand"])
        _event(client, r.json()["impression_id"], "rendered")
    assert len(lipid) == 1, lipid
    with db.tx() as conn:
        conn.execute("DELETE FROM campaigns WHERE id='c-rival'")
        conn.execute("DELETE FROM creatives WHERE brand_id='b-rival'")
        conn.execute("DELETE FROM brands WHERE id='b-rival'")
        conn.execute("DELETE FROM category_locks WHERE brand_id='b-rival'")
    client.post("/api/test/reset-caps")


def test_disabled_slot_serves_nothing(client):
    client.post("/api/network", json={"enabled_slots": ["schedule_card", "post_sign"], "default_cap": 3})
    r = _serve(client, slot="drafting_wait")
    assert r.status_code == 204 and r.headers["x-adzone-reason"] == "slot_disabled"
    client.post("/api/network", json={"enabled_slots": ["schedule_card", "drafting_wait", "post_sign"], "default_cap": 3})


# ---------------------------------------------------------------- hash chain

def test_hash_chain_verifies_and_detects_tampering(client):
    imp = _serve(client, prescriber=_network_prescriber()).json()["impression_id"]
    _event(client, imp, "rendered")
    ok = client.post("/api/verify-chain").json()
    assert ok["chain"]["ok"] is True and ok["chain"]["checked"] > 0
    assert ok["reconcile"]["ok"] is True
    with db.tx() as conn:
        seq = conn.execute("SELECT seq FROM events ORDER BY seq LIMIT 1 OFFSET 1").fetchone()["seq"]
        original = conn.execute("SELECT dwell_ms FROM events WHERE seq=?", (seq,)).fetchone()["dwell_ms"]
        conn.execute("UPDATE events SET dwell_ms=? WHERE seq=?", (original + 999, seq))
    bad = client.post("/api/verify-chain").json()
    assert bad["chain"]["ok"] is False and bad["chain"]["broken_at"] == seq
    with db.tx() as conn:
        conn.execute("UPDATE events SET dwell_ms=? WHERE seq=?", (original, seq))
    assert client.post("/api/verify-chain").json()["chain"]["ok"] is True


def test_hash_is_sha256_of_prev_plus_canonical_fields(client):
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    fields = {k: row[k] for k in events.HASH_FIELDS}
    assert events.compute_hash(row["prev_hash"], fields) == row["hash"]


# ---------------------------------------------------------------- one metric dictionary

def test_network_totals_reconcile_with_campaigns(client):
    rep = client.get("/api/overview").json()
    camp_served = sum(c["totals"]["served"] for c in rep["campaigns"])
    camp_viewable = sum(c["totals"]["viewable"] for c in rep["campaigns"])
    camp_spend = round(sum(c["totals"]["spend"] for c in rep["campaigns"]), 2)
    assert rep["totals"]["served"] == camp_served
    assert rep["totals"]["viewable"] == camp_viewable
    assert abs(rep["totals"]["spend"] - camp_spend) < 0.05
    assert sum(d["viewable"] for d in rep["daily"]) == rep["totals"]["viewable"]
    v = rep["totals"]
    assert abs(v["viewability"] - v["viewable"] / v["served"]) < 1e-9


def test_cpm_spend_is_viewable_times_cpm_capped_at_booked(client):
    with db.tx() as conn:
        c = [c for c in metrics.campaigns(conn) if c["id"] == "c-corvessa-q4"][0]
        rows = metrics.daily_rows(conn, c)
    viewable = sum(r["viewable"] for r in rows)
    expected = min(viewable, c["booked_viewable"]) / 1000 * c["cpm"]
    assert abs(sum(r["spend"] for r in rows) - expected) < 0.05


def test_sponsorship_spend_is_flat_fee_pro_rated(client):
    with db.tx() as conn:
        c = [c for c in metrics.campaigns(conn) if c["contract_type"] == "sponsorship"][0]
        rows = metrics.daily_rows(conn, c)
    expected = c["fee_per_prescriber_month"] * c["sponsored_prescribers"] * len(rows) / metrics.MONTH_DAYS
    assert abs(sum(r["spend"] for r in rows) - expected) < 0.5


def test_forecast_is_cap_limited(client):
    body = {"contract_type": "cpm", "cpm": 60, "start_date": "2026-12-01", "end_date": "2026-12-28",
            "specialties": ["cardiology"], "states": [], "slots": ["schedule_card", "drafting_wait"], "freq_cap_week": 3}
    f = client.post("/api/forecast", json=body).json()
    assert f["sellable_impressions"] <= f["slot_opportunities"]
    assert f["sellable_impressions"] == pytest.approx(f["eligible_prescribers"] * 3 * 4, rel=0.01)
    assert f["projected_revenue"] == pytest.approx(f["projected_viewable"] / 1000 * 60, rel=0.01)


# ---------------------------------------------------------------- share links and exports

def test_share_link_works_without_access_code(client, anon):
    camps = client.get("/api/campaigns").json()["campaigns"]
    token = next(c["share_token"] for c in camps if c["id"] == "c-thromvia-q4")
    r = anon.get(f"/share/{token}")
    assert r.status_code == 200
    data = r.json()
    assert data["read_only"] is True and data["simulated"] is True
    assert data["campaign"]["id"] == "c-thromvia-q4"
    assert anon.get("/share/not-a-real-token").status_code == 404
    assert anon.get("/api/campaigns").status_code == 401


def test_csv_is_day_level_and_marked_simulated(client, anon):
    text = client.get("/api/export.csv").text
    lines = text.splitlines()
    assert lines[0].startswith("# SIMULATED DATA")
    reader = list(csv.reader(io.StringIO("\n".join(lines[1:]))))
    header, rows = reader[0], reader[1:]
    assert header == metrics.CSV_COLUMNS
    assert rows
    for col in header:
        assert "prescriber" not in col and "npi" not in col and "time" != col and "hour" not in col
    for r in rows:
        assert len(r[0]) == 10 and r[0][4] == "-" and "T" not in r[0]
        assert r[-1] == "SIMULATED"
    token = next(c["share_token"] for c in client.get("/api/campaigns").json()["campaigns"])
    shared = anon.get(f"/share/{token}/export.csv")
    assert shared.status_code == 200 and shared.text.startswith("# SIMULATED DATA")


def test_create_campaign_and_changelog(client):
    body = {"brand_id": "b-quenvara", "name": "Quenvara test flight", "contract_type": "cpm", "cpm": 50,
            "booked_viewable": 2000, "start_date": serving.today().isoformat(), "end_date": "2099-01-31",
            "specialties": ["internal_medicine"], "states": ["NY"], "slots": ["schedule_card"], "freq_cap_week": 2}
    r = client.post("/api/campaigns", json=body)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert any(c["id"] == cid for c in client.get("/api/campaigns").json()["campaigns"])
    log = client.get("/api/changelog").json()["entries"]
    assert log[0]["entity_id"] == cid and log[0]["action"] == "created"
    assert client.patch(f"/api/campaigns/{cid}", json={"status": "paused"}).status_code == 200
    with db.tx() as conn:
        conn.execute("DELETE FROM campaigns WHERE id=?", (cid,))
