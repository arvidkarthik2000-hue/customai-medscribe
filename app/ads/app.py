"""Ad zone: a separate FastAPI app with its own database.

Mounted at /adzone by app.main, or run on its own host:
    uvicorn app.ads.app:app
It imports nothing from the clinical zone. Serving accepts exactly five fields:
slot, prescriber_id, specialty, state, hour (pydantic extra="forbid"), so visit
content cannot reach this zone even by mistake.
"""
from __future__ import annotations

import csv
import io
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import auth
from . import catalog, db, events, forecast, metrics, serving

app = FastAPI(title="MedScribe ad zone", docs_url=None, redoc_url=None)

_cors = [o.strip() for o in os.environ.get("ADZONE_CORS_ORIGINS", "").split(",") if o.strip()]
if _cors:  # only when the ad zone runs on its own host
    app.add_middleware(CORSMiddleware, allow_origins=_cors, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type"])


@app.middleware("http")
async def ready(request: Request, call_next):
    db.ensure_ready()
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


Specialty = Literal["family_medicine", "internal_medicine", "cardiology", "endocrinology", "pulmonology"]
ServedSlot = Literal["schedule_card", "drafting_wait", "post_sign"]
PrescriberId = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
StateCode = Field(pattern=r"^[A-Z]{2}$")


class ServeIn(BaseModel):
    """The only data the ad zone ever receives about a request."""
    model_config = ConfigDict(extra="forbid")
    slot: ServedSlot
    prescriber_id: str = PrescriberId
    specialty: Specialty
    state: str = StateCode
    hour: int = Field(ge=0, le=23)


class TrayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prescriber_id: str = PrescriberId
    specialty: Specialty
    state: str = StateCode
    hour: int = Field(ge=0, le=23)


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    impression_id: str = Field(pattern=r"^imp_[0-9a-f]{32}$")
    type: Literal["rendered", "viewable", "click", "isi_open", "hide", "dwell"]
    dwell_ms: int | None = Field(default=None, ge=0, le=600000)
    visible: bool | None = None
    webdriver: bool = False


class CampaignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand_id: str
    name: str = Field(min_length=3, max_length=80)
    contract_type: Literal["cpm", "sponsorship"]
    cpm: float | None = Field(default=None, ge=5, le=500)
    fee_per_prescriber_month: float | None = Field(default=None, ge=0.5, le=100)
    booked_viewable: int | None = Field(default=None, ge=1000, le=5_000_000)
    budget: float | None = Field(default=None, ge=100, le=10_000_000)
    start_date: date
    end_date: date
    specialties: list[Specialty] = Field(min_length=1)
    states: list[str] = Field(min_length=1)
    slots: list[ServedSlot] = Field(min_length=1)
    freq_cap_week: int = Field(default=3, ge=1, le=5)
    exclusivity: bool = True


class ForecastIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand_id: str | None = None
    contract_type: Literal["cpm", "sponsorship"] = "cpm"
    cpm: float | None = None
    fee_per_prescriber_month: float | None = None
    start_date: date
    end_date: date
    specialties: list[Specialty] = []
    states: list[str] = []
    slots: list[ServedSlot] = ["schedule_card", "drafting_wait", "post_sign"]
    freq_cap_week: int = Field(default=3, ge=1, le=5)


class CampaignPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "paused"] | None = None
    freq_cap_week: int | None = Field(default=None, ge=1, le=5)


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled_slots: list[ServedSlot]
    default_cap: int = Field(ge=1, le=5)


class AttestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approver: str = Field(default="Sponsor regulatory approver (fictional)", min_length=3, max_length=80)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(conn, entity: str, entity_id: str, action: str, detail: dict, actor: str = "Network console (demo user)") -> None:
    conn.execute(
        "INSERT INTO changelog(at, actor, entity, entity_id, action, detail) VALUES (?,?,?,?,?,?)",
        (_now(), actor, entity, entity_id, action, json.dumps(detail)),
    )


def _campaign(conn, campaign_id: str) -> dict:
    for c in metrics.campaigns(conn):
        if c["id"] == campaign_id:
            return c
    raise HTTPException(404, "Campaign not found")


# ---------------------------------------------------------------- serving (doctor app)

@app.get("/health")
def health():
    return {"ok": True, "zone": "ads"}


@app.post("/serve")
def serve(body: ServeIn, _s: dict = Depends(auth.require)):
    result, reason = serving.serve(body.slot, body.prescriber_id, body.specialty, body.state, body.hour)
    if result is None:
        return Response(status_code=204, headers={"X-Adzone-Reason": reason})
    return result


@app.post("/tray")
def tray(body: TrayIn, _s: dict = Depends(auth.require)):
    return serving.tray(body.prescriber_id, body.specialty, body.state, body.hour)


@app.post("/events")
def record_event(body: EventIn, request: Request, _s: dict = Depends(auth.require)):
    try:
        return events.record(
            body.impression_id, body.type, dwell_ms=body.dwell_ms, visible=body.visible,
            webdriver=body.webdriver, user_agent=request.headers.get("user-agent", ""),
        )
    except LookupError:
        raise HTTPException(404, "Unknown impression")


@app.get("/pi/{brand_id}", response_class=HTMLResponse)
def prescribing_information(brand_id: str):
    with db.tx() as conn:
        b = conn.execute("SELECT * FROM brands WHERE id=?", (brand_id,)).fetchone()
    if not b:
        raise HTTPException(404, "Not found")
    boxed = (
        f'<div style="border:2px solid #15202b;padding:12px 14px;margin:16px 0;border-radius:6px">'
        f'<strong>BOXED WARNING (fictional)</strong><p style="margin:6px 0 0">{escape(b["risks"])}</p></div>'
        if b["boxed_warning"] else ""
    )
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(b['name'])} prescribing information (fictional sample)</title>
<link rel="stylesheet" href="/css/app.css"></head>
<body style="background:#f5f4f0">
<main style="max-width:760px;margin:0 auto;padding:32px 20px">
<div style="background:#fff4d6;color:#7a4d00;border:1px solid #f0d78f;border-radius:8px;padding:10px 14px;font-weight:600">
Fictional prescribing information for a demo. {escape(b['name'])} is not a real product; nothing here is medical information.</div>
<h1 style="margin:24px 0 4px">{escape(b['name'])} ({escape(b['generic'])})</h1>
<p style="color:#47525e">{escape(b['company'])} (fictional) · {escape(b['category'])}</p>
{boxed}
<h2 style="margin-top:24px">Indication (fictional)</h2>
<p>{escape(b['name'])} is {escape(b['indication'])}.</p>
<h2 style="margin-top:20px">Important safety information (fictional)</h2>
<p>{escape(b['risks'])}</p>
<h2 style="margin-top:20px">About this page</h2>
<p>In production, each sponsored unit links to the sponsor's own FDA-approved prescribing information.
Sponsored content is clinician-facing only and never goes to patients.</p>
</main></body></html>"""
    return HTMLResponse(html)


# ---------------------------------------------------------------- console API

@app.get("/api/overview")
def overview(days: int = Query(30, ge=7, le=90), _s: dict = Depends(auth.require)):
    start, end = metrics.period(days)
    with db.tx() as conn:
        report = metrics.network_report(conn, start, end)
        report["inventory"] = forecast.inventory(conn)
        report["policy"] = serving.policy(conn)
        report["fingerprint"] = events.fingerprint(conn)
        report["live_today"] = _live_counters(conn)
        report["history"] = {"start": db.meta_get(conn, "history_start"), "end": db.meta_get(conn, "history_end"),
                             "seeded_at": db.meta_get(conn, "seeded_at")}
    return report


@app.get("/api/campaigns")
def list_campaigns(_s: dict = Depends(auth.require)):
    with db.tx() as conn:
        out = []
        for c in metrics.campaigns(conn):
            rows = metrics.daily_rows(conn, c)
            out.append({**metrics.public_campaign(c), "totals": metrics.summarize(rows),
                        "pacing": metrics.pacing(c, rows), "share_token": c["share_token"]})
        brands = [dict(b) for b in conn.execute("SELECT * FROM brands ORDER BY name")]
    for b in brands:
        b["company"] = f"{b['company']} (fictional)"
        b["boxed_warning"] = bool(b["boxed_warning"])
    return {"campaigns": out, "brands": brands, "specialties": catalog.SPECIALTIES,
            "states": {k: v[0] for k, v in catalog.STATES.items()},
            "slots": {k: v["label"] for k, v in catalog.SLOTS.items() if v["counted"]}}


@app.get("/api/campaigns/{campaign_id}")
def campaign_detail(campaign_id: str, _s: dict = Depends(auth.require)):
    with db.tx() as conn:
        c = _campaign(conn, campaign_id)
        report = metrics.campaign_report(conn, c)
        report["share_token"] = c["share_token"]
        report["creatives"] = _creatives(conn, c["brand_id"])
    return report


@app.post("/api/campaigns", status_code=201)
def create_campaign(body: CampaignIn, _s: dict = Depends(auth.require)):
    if body.end_date < body.start_date:
        raise HTTPException(422, "The flight must end on or after its start date.")
    bad_states = [s for s in body.states if s not in catalog.STATES]
    if bad_states:
        raise HTTPException(422, f"Unknown states: {', '.join(bad_states)}")
    with db.tx() as conn:
        brand = conn.execute("SELECT * FROM brands WHERE id=?", (body.brand_id,)).fetchone()
        if not brand:
            raise HTTPException(422, "Unknown brand")
        if body.contract_type == "cpm":
            if not body.cpm:
                raise HTTPException(422, "A CPM campaign needs a CPM.")
            booked = body.booked_viewable or (int(body.budget / body.cpm * 1000) if body.budget else None)
            if not booked:
                raise HTTPException(422, "Give booked viewable impressions or a budget.")
        else:
            if not body.fee_per_prescriber_month:
                raise HTTPException(422, "A sponsorship needs a fee per prescriber per month.")
            booked = None
        if body.exclusivity:
            clash = conn.execute(
                "SELECT c.name, c.end_date, b.name brand FROM campaigns c JOIN brands b ON b.id=c.brand_id "
                "WHERE b.category=? AND c.brand_id!=? AND c.status NOT IN ('paused','draft') AND c.end_date>=? AND c.start_date<=?",
                (brand["category"], brand["id"], body.start_date.isoformat(), body.end_date.isoformat()),
            ).fetchone()
            if clash:
                raise HTTPException(409, f"{brand['category']} is held by {clash['brand']} until {clash['end_date']} "
                                         "(one brand per category per office).")
        missing = [s for s in body.slots if not serving.eligible_creatives(conn, brand, s, body.start_date)]
        cid = f"c-{brand['id'][2:]}-{secrets.token_hex(3)}"
        spec = {"specialties": json.dumps(body.specialties), "states": json.dumps(body.states)}
        sponsored = None
        if body.contract_type == "sponsorship":
            sponsored = len(forecast._prescribers(conn, body.specialties, body.states))
        conn.execute(
            "INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, brand["id"], body.name, body.contract_type, body.cpm, body.fee_per_prescriber_month, booked,
             round(booked / 1000 * body.cpm, 2) if booked and body.cpm else None,
             body.start_date.isoformat(), body.end_date.isoformat(), spec["specialties"], spec["states"],
             json.dumps(body.slots), body.freq_cap_week, int(body.exclusivity), "active",
             secrets.token_urlsafe(16), sponsored, "pending_counsel", _now(), "network_console"),
        )
        _log(conn, "campaign", cid, "created", {"name": body.name, "brand": brand["name"], "contract": body.contract_type,
                                               "booked_viewable": booked, "cpm": body.cpm,
                                               "fee_per_prescriber_month": body.fee_per_prescriber_month})
    return {"id": cid, "warnings": [f"No approved creative for {catalog.SLOTS[s]['label']}" for s in missing]}


@app.patch("/api/campaigns/{campaign_id}")
def patch_campaign(campaign_id: str, body: CampaignPatch, _s: dict = Depends(auth.require)):
    with db.tx() as conn:
        _campaign(conn, campaign_id)
        changes = body.model_dump(exclude_none=True)
        for k, v in changes.items():
            conn.execute(f"UPDATE campaigns SET {k}=? WHERE id=?", (v, campaign_id))
        if changes:
            _log(conn, "campaign", campaign_id, "updated", changes)
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/share/rotate")
def rotate_share(campaign_id: str, _s: dict = Depends(auth.require)):
    token = secrets.token_urlsafe(16)
    with db.tx() as conn:
        _campaign(conn, campaign_id)
        conn.execute("UPDATE campaigns SET share_token=? WHERE id=?", (token, campaign_id))
        _log(conn, "campaign", campaign_id, "share_link_rotated", {})
    return {"share_token": token}


@app.post("/api/forecast")
def run_forecast(body: ForecastIn, _s: dict = Depends(auth.require)):
    with db.tx() as conn:
        return forecast.forecast(
            conn, specialties=body.specialties, states=body.states, slots=body.slots,
            freq_cap_week=body.freq_cap_week, contract_type=body.contract_type, cpm=body.cpm,
            fee_per_prescriber_month=body.fee_per_prescriber_month, start_date=body.start_date.isoformat(),
            end_date=body.end_date.isoformat(), brand_id=body.brand_id,
        )


@app.get("/api/inventory")
def get_inventory(_s: dict = Depends(auth.require)):
    with db.tx() as conn:
        return forecast.inventory(conn)


@app.get("/api/network")
def get_policy(_s: dict = Depends(auth.require)):
    with db.tx() as conn:
        return serving.policy(conn)


@app.post("/api/network")
def set_policy(body: PolicyIn, _s: dict = Depends(auth.require)):
    with db.tx() as conn:
        for k, v in body.model_dump().items():
            conn.execute(
                "INSERT INTO network_policy VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v))
            )
        _log(conn, "network", "ad_load", "updated", body.model_dump())
        return {"policy": serving.policy(conn), "inventory": forecast.inventory(conn)}


def _creatives(conn, brand_id: str | None = None) -> list[dict]:
    t = date.today().isoformat()
    q = ("SELECT cr.*, b.name brand_name, b.category, b.boxed_warning FROM creatives cr JOIN brands b ON b.id=cr.brand_id"
         + (" WHERE cr.brand_id=?" if brand_id else "") + " ORDER BY b.name, cr.version")
    out = []
    for r in conn.execute(q, (brand_id,) if brand_id else ()):
        d = dict(r)
        d["slots"] = json.loads(d["slots"])
        d["boxed_warning"] = bool(d["boxed_warning"])
        days_left = (date.fromisoformat(d["mlr_expires"]) - date.fromisoformat(t)).days
        d["mlr_days_left"] = days_left
        if d["status"] == "approved" and days_left < 0:
            d["status"] = "expired"
        d["servable"] = d["status"] == "approved"
        d["pi_path"] = f"/pi/{d['brand_id']}"
        out.append(d)
    return out


@app.get("/api/creatives")
def list_creatives(_s: dict = Depends(auth.require)):
    with db.tx() as conn:
        return {"creatives": _creatives(conn), "formats": catalog.FORMATS,
                "rules": [
                    "Reminder cards (name only) are blocked for any brand with a boxed warning.",
                    "Claim cards carry the most serious risks inside the unit and link to prescribing information.",
                    "A unit is served only while its MLR approval is current.",
                    "Production runs only sponsor-approved creative; the demo's brands and creative are fictional samples.",
                    "Creative must be fine for a patient to see: the screen may face the exam table.",
                ]}


@app.post("/api/creatives/{creative_id}/attest")
def attest(creative_id: str, body: AttestIn, _s: dict = Depends(auth.require)):
    with db.tx() as conn:
        if not conn.execute("SELECT 1 FROM creatives WHERE id=?", (creative_id,)).fetchone():
            raise HTTPException(404, "Creative not found")
        conn.execute("UPDATE creatives SET approver=?, attested_at=? WHERE id=?",
                     (body.approver, date.today().isoformat(), creative_id))
        _log(conn, "creative", creative_id, "attested", {"approver": body.approver})
    return {"ok": True}


@app.get("/api/changelog")
def changelog(limit: int = Query(100, ge=1, le=500), _s: dict = Depends(auth.require)):
    with db.tx() as conn:
        rows = conn.execute("SELECT * FROM changelog ORDER BY at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return {"entries": [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in rows]}


def _live_counters(conn) -> dict:
    t = date.today().isoformat()
    rows = conn.execute(
        "SELECT traffic, SUM(rendered) served, SUM(viewable) viewable FROM delivery_daily "
        "WHERE source='live' AND day=? AND slot!='resources_tray' GROUP BY traffic", (t,),
    ).fetchall()
    out = {k: {"served": 0, "viewable": 0} for k in ("valid", "test", "ivt")}
    for r in rows:
        out[r["traffic"]] = {"served": r["served"], "viewable": r["viewable"]}
    return out


@app.get("/api/live")
def live(after: int = Query(0, ge=0), limit: int = Query(40, ge=1, le=200), _s: dict = Depends(auth.require)):
    """Test and invalid traffic as it arrives (QA view). Valid delivery is only ever shown
    as day-level counts, so no one can read visit times off this feed."""
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT e.*, b.name brand_name FROM events e LEFT JOIN brands b ON b.id=e.brand_id "
            "WHERE e.seq>? AND e.traffic IN ('test','ivt') ORDER BY e.seq DESC LIMIT ?", (after, limit),
        ).fetchall()
        head = conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
        counters = _live_counters(conn)
    items = [{
        "seq": r["seq"], "time": r["at"][11:19], "type": r["type"],
        "slot": catalog.SLOTS.get(r["slot"], {}).get("label", r["slot"]), "brand": r["brand_name"],
        "campaign_id": r["campaign_id"], "traffic": r["traffic"], "reason": r["reason"],
        "prescriber": "Test account" if r["traffic"] == "test" else f"•••{(r['prescriber_id'] or '')[-4:]}",
        "specialty": catalog.specialty_label(r["specialty"]), "state": r["state"],
    } for r in rows]
    return {"items": items, "head_seq": head, "today": counters}


@app.post("/api/test/reset-caps")
def reset_test_caps(_s: dict = Depends(auth.require)):
    with db.tx() as conn:
        db.meta_set(conn, "test_cap_reset_at", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
        conn.execute("DELETE FROM hides WHERE prescriber_id IN (SELECT id FROM prescribers WHERE is_test=1)")
        _log(conn, "network", "test_accounts", "caps_reset", {"note": "Test-account caps and hides cleared; events stay in the log."})
    return {"ok": True}


@app.get("/api/verification")
def verification(_s: dict = Depends(auth.require)):
    with db.tx() as conn:
        last = json.loads(db.meta_get(conn, "last_verification", "null") or "null")
        return {
            "fingerprint": events.fingerprint(conn),
            "last_verification": last,
            "audit_status": [
                {"name": "POCMA certification (AAM audit)", "status": "Not yet audited"},
                {"name": "MRC accreditation", "status": "Not accredited"},
                {"name": "Third-party verification", "status": "Server-side log audit only; no third-party code runs in the app"},
            ],
            "method": metrics.DEFINITIONS,
            "history_note": ("The 30-day history is simulated: daily roll-ups generated for a 500-office demo network, "
                             "within the same caps the ad server enforces. Live demo traffic is logged event by event in "
                             "the hash-chained log and always marked as test traffic."),
            "privacy": ["The ad zone receives only slot, prescriber ID, declared specialty and state, and hour of day.",
                        "No visit content, notes or patient data ever reach the ad zone, Constant Media or sponsors.",
                        "Sponsor reports and exports carry day-level counts only, never times of day or visit identifiers."],
        }


@app.post("/api/verify-chain")
def verify_chain(_s: dict = Depends(auth.require)):
    with db.tx() as conn:
        chain = events.verify_chain(conn)
        rec = events.reconcile(conn)
        result = {"chain": chain, "reconcile": rec, "at": _now()}
        db.meta_set(conn, "last_verification", json.dumps(result))
    return result


def _csv_response(rows: list[list], filename: str) -> Response:
    buf = io.StringIO()
    buf.write("# SIMULATED DATA: CustomAi MedScribe demo network. Not real campaign results. Day-level counts only.\n")
    w = csv.writer(buf)
    w.writerow(metrics.CSV_COLUMNS)
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/export.csv")
def export_csv(campaign_id: str | None = None, days: int | None = Query(None, ge=1, le=365),
               _s: dict = Depends(auth.require)):
    with db.tx() as conn:
        camps = [_campaign(conn, campaign_id)] if campaign_id else metrics.campaigns(conn)
        start = metrics.period(days)[0] if days else None
        rows = []
        for c in camps:
            rows += metrics.csv_rows(c, metrics.daily_rows(conn, c, start))
    rows.sort(key=lambda r: (r[0], r[1]))
    name = f"medscribe-SIMULATED-{campaign_id or 'network'}-day-level.csv"
    return _csv_response(rows, name)


@app.get("/api/sponsor/campaigns")
def sponsor_campaigns(_s: dict = Depends(auth.require)):
    with db.tx() as conn:
        out = []
        for c in metrics.campaigns(conn):
            if c["effective_status"] == "scheduled":
                continue
            rows = metrics.daily_rows(conn, c)
            out.append({**metrics.public_campaign(c), "totals": metrics.summarize(rows),
                        "pacing": metrics.pacing(c, rows), "share_token": c["share_token"]})
    companies = sorted({c["company"] for c in out})
    return {"campaigns": out, "companies": companies}


# ---------------------------------------------------------------- read-only share links (no access code)

def _by_token(conn, token: str) -> dict:
    if not token or len(token) > 64:
        raise HTTPException(404, "This report link is not valid.")
    for c in metrics.campaigns(conn):
        if c["share_token"] and secrets.compare_digest(c["share_token"], token):
            return c
    raise HTTPException(404, "This report link is not valid or has been revoked.")


@app.get("/share/{token}")
def shared_report(token: str):
    with db.tx() as conn:
        c = _by_token(conn, token)
        report = metrics.campaign_report(conn, c)
    report["read_only"] = True
    return JSONResponse(report)


@app.get("/share/{token}/export.csv")
def shared_csv(token: str):
    with db.tx() as conn:
        c = _by_token(conn, token)
        rows = metrics.csv_rows(c, metrics.daily_rows(conn, c))
    return _csv_response(rows, f"medscribe-SIMULATED-{c['id']}-day-level.csv")


def startup() -> None:
    """Optional: the lead may call this at app start to seed before the first request."""
    db.ensure_ready()
