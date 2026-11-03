"""HTTP routes for drugs and formulary, on a minimal app with only this router."""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-drugs-"))

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app import auth
from app.drugs import net
from app.drugs.routes import router


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DRUGS_OFFLINE", "1")
    net.reset()
    app = FastAPI()
    app.include_router(router, prefix="/api")

    @app.post("/login")
    def login(response: Response):
        auth.issue(response, "pharmacy")
        return {"ok": True}

    c = TestClient(app)
    yield c
    net.reset()


def signed_in(c):
    assert c.post("/login").status_code == 200
    return c


def test_requires_session(client):
    assert client.post("/api/drugs/check", json={"medications": [{"name": "warfarin"}]}).status_code == 401
    assert client.get("/api/formulary/plans").status_code == 401


def test_check_route(client):
    c = signed_in(client)
    r = c.post("/api/drugs/check", json={
        "medications": [{"name": "warfarin"}, {"name": "ibuprofen", "dose": "400", "unit": "mg", "frequency": "three times daily"}],
        "patient": {"age": 70, "sex": "F", "creatinine": 1.1, "allergies": ["sulfa"], "problems": ["atrial fibrillation"], "plan_key": "MAPD-B"},
        "include_prevention": True,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["cards"][0]["indicator"] in ("critical", "warning")
    assert any(c["category"] == "interaction" for c in data["cards"])
    assert data["patient_context"]["egfr"] is not None
    assert any(p["id"] == "zoster" for p in data["prevention"])


def test_check_validation(client):
    c = signed_in(client)
    assert c.post("/api/drugs/check", json={"medications": []}).status_code == 422


def test_search(client):
    c = signed_in(client)
    res = c.get("/api/drugs/search", params={"q": "lisin"}).json()["results"]
    assert res[0]["name"] == "lisinopril"
    brand = c.get("/api/drugs/search", params={"q": "eliq"}).json()["results"]
    assert brand[0]["generic"] == "apixaban" and brand[0]["tty"] == "BN"


def test_formulary(client):
    c = signed_in(client)
    plans = c.get("/api/formulary/plans").json()["plans"]
    assert {p["plan_key"] for p in plans} == {"PDP-A", "MAPD-B", "COMMERCIAL"}
    look = c.get("/api/formulary/lookup", params={"plan_key": "MAPD-B", "drug": "Farxiga"}).json()
    assert look["drug"]["generic"] == "dapagliflozin"
    assert look["status"]["tier"] == 4 and look["status"]["step_therapy"] is True
    assert look["alternatives"] and look["alternatives"][0]["drug"] == "empagliflozin"
    assert "demo data" in look["source"]
    com = c.get("/api/formulary/lookup", params={"plan_key": "COMMERCIAL", "drug": "apixaban"}).json()
    assert com["status"] is None and "real-time benefit" in com["note"]
    assert c.get("/api/formulary/lookup", params={"plan_key": "NOPE", "drug": "x"}).status_code == 404


def test_calc_route(client):
    c = signed_in(client)
    ok = c.post("/api/drugs/calc", json={"calculator": "bmi", "inputs": {"weight_kg": 70, "height_cm": 175}}).json()
    assert ok["value"] == 22.9
    bad = c.post("/api/drugs/calc", json={"calculator": "ckd_epi_2021", "inputs": {"age": 50}})
    assert bad.status_code == 422


def test_sources_route(client):
    c = signed_in(client)
    s = c.get("/api/drugs/sources").json()
    assert s["onc_pairs"] == 15 and s["onc_rules"] >= 15 and s["rules"] >= 100
