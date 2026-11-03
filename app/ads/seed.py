"""Deterministic seed for the simulated network and its delivery history.

- ~500 offices and ~610 prescribers (no names; pseudo-NPIs start with 9)
- 10 fictional brands, their creatives and 11 campaigns (CPM and flat sponsorship)
- 30 days of delivery written as daily roll-ups (source = 'simulated'), generated
  from a per-prescriber weekly plan that never exceeds the frequency cap
- history rolls forward one day at a time, so a demo left running stays current

Live tester traffic is never simulated: it arrives through the hash-chained event
log (source = 'live').
"""
from __future__ import annotations

import hashlib
import hmac
import json
import random
from datetime import date, datetime, timedelta, timezone

from ..config import settings
from . import catalog, db
from .creative_art import brand_art  # noqa: F401  (imported so artwork errors surface at seed time)

SEED_VERSION = "5"
RNG_SEED = 20261103
HISTORY_DAYS = 30

N_OFFICES = 500

# slot -> probability a rendered unit becomes viewable (MRC: 50% of pixels for 1 s)
VIEWABLE_P = {"schedule_card": 0.74, "drafting_wait": 0.88, "post_sign": 0.82}
DWELL_MEAN_MS = {"schedule_card": 6200, "drafting_wait": 11400, "post_sign": 4600}
SLOT_WEIGHT = {"schedule_card": 0.42, "drafting_wait": 0.34, "post_sign": 0.24}
IVT_P = 0.018
CLICK_P = 0.007
ISI_P = 0.016
HIDE_P = 0.0025


def today() -> date:
    return datetime.now(timezone.utc).date()


def iso(d: date) -> str:
    return d.isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def share_token_for(campaign_id: str) -> str:
    """Stable across restarts while APP_SECRET is stable, so share links survive a reseed."""
    mac = hmac.new(settings.app_secret.encode(), f"share:{campaign_id}".encode(), hashlib.sha256).hexdigest()
    return mac[:22]


# ---------------------------------------------------------------- campaign plan

def _campaign_specs(t: date) -> list[dict]:
    all_states = list(catalog.STATES)
    pc = ["family_medicine", "internal_medicine"]
    return [
        dict(id="c-corvessa-q4", brand="b-corvessa", name="Corvessa Q4 HCP awareness", contract="cpm", cpm=62.0,
             spec=pc + ["cardiology"], states=all_states, slots=["schedule_card", "drafting_wait", "post_sign"],
             start=t - timedelta(days=24), end=t + timedelta(days=36), lam=0.80, pacing=0.97),
        dict(id="c-glyvora-sponsor", brand="b-glyvora", name="Glyvora category sponsorship", contract="sponsorship",
             fee=2.0, spec=["endocrinology"] + pc, states=["NJ", "NY", "PA", "CT", "MA", "FL", "TX"],
             slots=["schedule_card", "drafting_wait", "post_sign"], start=t - timedelta(days=40), end=t + timedelta(days=50), lam=0.90),
        dict(id="c-aerilune-q4", brand="b-aerilune", name="Aerilune maintenance Q4", contract="cpm", cpm=70.0,
             spec=["pulmonology", "family_medicine"], states=all_states, slots=["schedule_card", "drafting_wait"],
             start=t - timedelta(days=11), end=t + timedelta(days=49), lam=0.66, pacing=0.88),
        dict(id="c-derevia-fall", brand="b-derevia", name="Derevia fall flight", contract="cpm", cpm=55.0,
             spec=pc, states=["NJ", "NY", "PA", "MD", "VA", "NC"], slots=["schedule_card", "drafting_wait", "post_sign"],
             start=t - timedelta(days=28), end=t + timedelta(days=2), lam=0.82, pacing=1.01),
        dict(id="c-nevalto-launch", brand="b-nevalto", name="Nevalto launch wave 1", contract="cpm", cpm=58.0,
             spec=pc, states=all_states, slots=["schedule_card", "drafting_wait", "post_sign"],
             start=t - timedelta(days=10), end=t + timedelta(days=50), lam=0.76, pacing=0.93),
        dict(id="c-cardelion-sponsor", brand="b-cardelion", name="Cardelion cardiology sponsorship", contract="sponsorship",
             fee=2.5, spec=["cardiology"], states=all_states, slots=["schedule_card", "drafting_wait", "post_sign"],
             start=t - timedelta(days=30), end=t + timedelta(days=60), lam=0.90),
        dict(id="c-thromvia-q4", brand="b-thromvia", name="Thromvia AF claim campaign", contract="cpm", cpm=85.0,
             spec=["cardiology", "internal_medicine"], states=all_states, slots=["schedule_card", "drafting_wait"],
             start=t - timedelta(days=18), end=t + timedelta(days=42), lam=0.84, pacing=1.04),
        dict(id="c-leantris-south", brand="b-leantris", name="Leantris southern states", contract="cpm", cpm=75.0,
             spec=["endocrinology"] + pc, states=["TX", "FL", "GA", "NC", "IL"], slots=["schedule_card", "drafting_wait"],
             start=t - timedelta(days=14), end=t + timedelta(days=46), lam=0.60, pacing=0.82),
        dict(id="c-vaxcendra-season", brand="b-vaxcendra", name="Vaxcendra season education", contract="cpm", cpm=45.0,
             spec=list(catalog.SPECIALTIES), states=all_states, slots=["post_sign"],
             start=t - timedelta(days=26), end=t + timedelta(days=34), lam=0.80, pacing=0.99),
        dict(id="c-quenvara-q1", brand="b-quenvara", name="Quenvara Q1 launch", contract="cpm", cpm=52.0,
             spec=pc + ["cardiology"], states=all_states, slots=["schedule_card", "drafting_wait", "post_sign"],
             start=t + timedelta(days=5), end=t + timedelta(days=65), lam=0.75, pacing=1.0),
        dict(id="c-aerilune-fall", brand="b-aerilune", name="Aerilune fall launch", contract="cpm", cpm=64.0,
             spec=["pulmonology"], states=all_states, slots=["schedule_card", "drafting_wait"],
             start=t - timedelta(days=75), end=t - timedelta(days=12), lam=0.85, pacing=0.98),
    ]


# ---------------------------------------------------------------- seed

def ensure_seeded() -> None:
    with db.tx() as conn:
        if db.meta_get(conn, "seed_version") != SEED_VERSION:
            _wipe(conn)
            _seed_static(conn)
        _roll_forward(conn)


def _wipe(conn) -> None:
    for table in ("offices", "prescribers", "brands", "creatives", "campaigns", "changelog", "decisions",
                  "events", "delivery_daily", "hides", "category_locks", "network_policy", "meta"):
        conn.execute(f"DELETE FROM {table}")


def _seed_static(conn) -> None:
    rng = random.Random(RNG_SEED)
    t = today()

    # offices and prescribers
    specs = list(catalog.SPECIALTY_MIX)
    spec_w = [catalog.SPECIALTY_MIX[s] for s in specs]
    states = list(catalog.STATE_MIX)
    state_w = [catalog.STATE_MIX[s] for s in states]
    offices, prescribers = [], []
    n_presc = 0
    for i in range(1, N_OFFICES + 1):
        sp = rng.choices(specs, spec_w)[0]
        st = rng.choices(states, state_w)[0]
        city = rng.choice(catalog.STATES[st][1])
        oid = f"O{i:04d}"
        offices.append((oid, f"Sim office {i:04d}", sp, st, city, rng.choice([1, 1, 2, 2, 2, 3]), rng.choice([5, 5, 5, 5, 6]), 0, 1))
        for _ in range(2 if rng.random() < 0.22 else 1):
            n_presc += 1
            visits = int(max(280, min(560, rng.gauss(420, 60))))
            prescribers.append((
                f"P{n_presc:04d}", f"9{100000000 + n_presc * 1013:09d}", oid, sp, st, visits, 0,
                1 if rng.random() < 0.97 else 0, 1 if rng.random() < 0.985 else 0,
            ))
    offices.append((catalog.TEST_OFFICE_ID, "Demo practice (test account)", "family_medicine", "NJ", "Maplewood", 1, 5, 1, 1))
    prescribers.append(("DEMO-001", "9000000001", catalog.TEST_OFFICE_ID, "family_medicine", "NJ", 420, 1, 1, 1))
    conn.executemany("INSERT INTO offices VALUES (?,?,?,?,?,?,?,?,?)", offices)
    conn.executemany("INSERT INTO prescribers VALUES (?,?,?,?,?,?,?,?,?)", prescribers)

    # brands and creatives
    for b in catalog.BRANDS:
        conn.execute(
            "INSERT INTO brands VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (b["id"], b["name"], b["generic"], b["category"], b["company"], int(b["boxed_warning"]),
             b["color"], b["motif"], b["indication"], b["claim"], b["risks"]),
        )
        _seed_creatives(conn, rng, b, t)

    # campaigns
    for c in _campaign_specs(t):
        brand = catalog.BRAND_BY_ID[c["brand"]]
        status = "scheduled" if c["start"] > t else ("ended" if c["end"] < t else "active")
        conn.execute(
            "INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (c["id"], c["brand"], c["name"], c["contract"], c.get("cpm"), c.get("fee"), None, None,
             iso(c["start"]), iso(c["end"]), json.dumps(c["spec"]), json.dumps(c["states"]), json.dumps(c["slots"]),
             3, 1, status, share_token_for(c["id"]), None, "pending_counsel",
             now_iso(), "seed"),
        )
        conn.execute(
            "INSERT INTO changelog(at, actor, entity, entity_id, action, detail) VALUES (?,?,?,?,?,?)",
            (iso(c["start"] - timedelta(days=7)) + "T15:00:00+00:00", "Constant Media ad ops (simulated)", "campaign",
             c["id"], "created", json.dumps({"name": c["name"], "brand": brand["name"], "contract": c["contract"]})),
        )

    policy = {"enabled_slots": ["schedule_card", "drafting_wait", "post_sign"], "default_cap": 3}
    for k, v in policy.items():
        conn.execute("INSERT INTO network_policy VALUES (?,?)", (k, json.dumps(v)))

    # Simulate from the earliest flight start (or 30 days back) so flight-to-date totals are complete.
    start = min([t - timedelta(days=HISTORY_DAYS)] + [c["start"] for c in _campaign_specs(t) if c["start"] < t])
    db.meta_set(conn, "seed_version", SEED_VERSION)
    db.meta_set(conn, "seeded_at", now_iso())
    db.meta_set(conn, "history_start", iso(start))
    db.meta_set(conn, "history_end", iso(start - timedelta(days=1)))  # nothing simulated yet
    db.meta_set(conn, "test_cap_reset_at", "1970-01-01T00:00:00+00:00")
    db.meta_set(conn, "plan_specs", json.dumps({c["id"]: {"lam": c["lam"], "pacing": c.get("pacing")} for c in _campaign_specs(t)}))

    _assign_locks(conn)


def _seed_creatives(conn, rng: random.Random, b: dict, t: date) -> None:
    abbr = b["name"][:3].upper()
    company = b["company"]
    rows = []

    def add(version, fmt, slots, headline, body, risk, cta, status, expires_days, attested_days):
        rows.append((
            f"cr-{b['id'][2:]}-v{version}", b["id"], version, fmt, json.dumps(slots), headline, body, risk, cta,
            f"MLR-{abbr}-2026-{rng.randint(100, 9999):04d}", iso(t + timedelta(days=expires_days)),
            f"{company} regulatory review (fictional)", iso(t - timedelta(days=attested_days)), status,
            iso(t - timedelta(days=attested_days + 2)),
        ))

    name_line = f"{b['name']} ({b['generic']})"
    reminder_status = "blocked_boxed_warning" if b["boxed_warning"] else "approved"
    # Aerilune's reminder card carries an expired MLR approval, so it is never served.
    reminder_exp = -6 if b["id"] == "b-aerilune" else rng.randint(40, 300)
    if b["id"] == "b-aerilune":
        reminder_status = "expired"
    add(1, "reminder", ["schedule_card", "drafting_wait"], name_line, "", "", "Prescribing information",
        reminder_status, reminder_exp, rng.randint(30, 90))
    claim_exp = 9 if b["id"] == "b-nevalto" else rng.randint(30, 320)  # one approval expiring soon
    add(2, "claim", ["schedule_card", "drafting_wait"], b["claim"], f"{name_line} is {b['indication']}.",
        b["risks"], "Prescribing information", "approved", claim_exp, rng.randint(10, 60))
    if b["id"] in ("b-vaxcendra", "b-derevia", "b-nevalto"):
        add(3, "education", ["post_sign"], f"Clinician education: {b['category'].lower()}",
            f"A short clinical review on {b['category'].lower()} for your next visit, from {company} (fictional).",
            b["risks"], "Read the review", "approved", rng.randint(60, 300), rng.randint(5, 40))
    else:
        add(3, "affordability", ["post_sign"], f"{b['name']} Access Program (fictional)",
            "Benefits verification and prior-authorization support for your office staff. Information for clinicians only.",
            b["risks"], "Program details", "approved", rng.randint(60, 300), rng.randint(5, 40))
    add(4, "resource", ["resources_tray"], f"{b['name']} prescribing information",
        f"Full prescribing information and important safety information for {name_line}.", b["risks"],
        "Open", "approved", rng.randint(90, 360), rng.randint(5, 60))
    conn.executemany("INSERT INTO creatives VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def _assign_locks(conn) -> None:
    """One brand per therapeutic category per office: lock each office's category to the
    brand whose active campaign reaches it (first by start date)."""
    camps = conn.execute(
        "SELECT c.*, b.category FROM campaigns c JOIN brands b ON b.id=c.brand_id "
        "WHERE c.status IN ('active','scheduled') ORDER BY c.start_date"
    ).fetchall()
    for c in camps:
        for p in eligible_prescribers(conn, c):
            conn.execute(
                "INSERT OR IGNORE INTO category_locks VALUES (?,?,?,?,?)",
                (p["office_id"], c["category"], c["brand_id"], c["id"], c["end_date"]),
            )


# ---------------------------------------------------------------- eligibility and plans

def eligible_prescribers(conn, campaign, include_test: bool = False):
    specs = json.loads(campaign["specialties"] or "[]")
    states = json.loads(campaign["states"] or "[]")
    rows = conn.execute(
        "SELECT * FROM prescribers WHERE active=1 AND verified=1" + ("" if include_test else " AND is_test=0")
    ).fetchall()
    return [p for p in rows if (not specs or p["specialty"] in specs) and (not states or p["state"] in states)]


def _usage(pid: str) -> float:
    h = int(hashlib.sha256(pid.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return 0.75 + 0.30 * h


def week_plan(campaign_id: str, prescriber_id: str, week_start: date, lam: float, cap: int,
              open_days: int, flight: tuple[date, date], slots: list[str]) -> list[tuple[date, str]]:
    """Deterministic renders for one prescriber-week: never more than `cap`."""
    rng = random.Random(f"{campaign_id}|{prescriber_id}|{week_start.isoformat()}")
    p = min(0.98, lam * _usage(prescriber_id))
    n = sum(1 for _ in range(cap) if rng.random() < p)
    days = [week_start + timedelta(days=i) for i in range(7)]
    days = [d for d in days if d.weekday() < open_days and flight[0] <= d <= flight[1]]
    if not days or n == 0:
        return []
    chosen = sorted(rng.sample(days, n) if n <= len(days) else [rng.choice(days) for _ in range(n)])
    weights = [SLOT_WEIGHT[s] for s in slots]
    return [(d, rng.choices(slots, weights)[0]) for d in chosen]


def _roll_forward(conn) -> None:
    """Simulate every missing day from history_end+1 to yesterday."""
    t = today()
    end = date.fromisoformat(db.meta_get(conn, "history_end"))
    target = t - timedelta(days=1)
    if end >= target:
        return
    plan_specs = json.loads(db.meta_get(conn, "plan_specs", "{}"))
    start_sim = end + timedelta(days=1)
    camps = conn.execute(
        "SELECT c.*, b.category FROM campaigns c JOIN brands b ON b.id=c.brand_id"
    ).fetchall()
    creatives = {}
    for cr in conn.execute("SELECT * FROM creatives WHERE status='approved'").fetchall():
        for s in json.loads(cr["slots"]):
            creatives.setdefault((cr["brand_id"], s), []).append(cr)
    offices = {o["id"]: o for o in conn.execute("SELECT * FROM offices").fetchall()}

    agg: dict[tuple, list[int]] = {}
    for c in camps:
        spec = plan_specs.get(c["id"])
        if not spec:
            continue  # campaigns created in the console get live traffic only
        flight = (date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"]))
        lo, hi = max(start_sim, flight[0]), min(target, flight[1])
        if lo > hi:
            continue
        slots = json.loads(c["slots"])
        for p in eligible_prescribers(conn, c):
            o = offices[p["office_id"]]
            week0 = flight[0]
            wk = week0 + timedelta(days=7 * ((lo - week0).days // 7))
            while wk <= hi:
                plan = week_plan(c["id"], p["id"], wk, spec["lam"], c["freq_cap_week"], o["open_days"], flight, slots)
                for k, (d, slot) in enumerate(plan):
                    if lo <= d <= hi:
                        _simulate_render(agg, c, p, d, slot, k, creatives)
                wk += timedelta(days=7)

    rows = [(*k, *v) for k, v in agg.items()]
    conn.executemany(
        "INSERT INTO delivery_daily (day, campaign_id, creative_id, brand_id, prescriber_id, office_id, specialty, state, "
        "slot, source, traffic, rendered, viewable, clicks, isi_opens, hides, dwell_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(day, campaign_id, creative_id, prescriber_id, slot, source, traffic) DO UPDATE SET "
        "rendered=rendered+excluded.rendered, viewable=viewable+excluded.viewable, clicks=clicks+excluded.clicks, "
        "isi_opens=isi_opens+excluded.isi_opens, hides=hides+excluded.hides, dwell_ms=dwell_ms+excluded.dwell_ms",
        rows,
    )
    db.meta_set(conn, "history_end", iso(target))
    _book_campaigns(conn, plan_specs)


def _simulate_render(agg, c, p, d: date, slot: str, k: int, creatives) -> None:
    options = creatives.get((c["brand_id"], slot)) or []
    if not options:
        return
    rng = random.Random(f"r|{c['id']}|{p['id']}|{d.isoformat()}|{slot}|{k}")
    cr = rng.choice(options)
    traffic = "ivt" if rng.random() < IVT_P else "valid"
    viewable = 1 if rng.random() < VIEWABLE_P[slot] else 0
    has_isi = cr["format"] in ("claim", "affordability", "education")
    clicks = 1 if viewable and rng.random() < CLICK_P else 0
    isi = 1 if viewable and has_isi and rng.random() < ISI_P else 0
    hides = 1 if rng.random() < HIDE_P else 0
    dwell = int(rng.lognormvariate(0, 0.45) * DWELL_MEAN_MS[slot] / 1.106) if viewable else 0
    key = (d.isoformat(), c["id"], cr["id"], c["brand_id"], p["id"], p["office_id"], p["specialty"], p["state"],
           slot, "simulated", traffic)
    cur = agg.setdefault(key, [0, 0, 0, 0, 0, 0])
    cur[0] += 1
    cur[1] += viewable
    cur[2] += clicks
    cur[3] += isi
    cur[4] += hides
    cur[5] += dwell


def _book_campaigns(conn, plan_specs: dict) -> None:
    """Set booked impressions (CPM) and sponsored prescriber counts (sponsorships) once,
    from the first simulated window, so pacing lands near each campaign's target."""
    t = today()
    for c in conn.execute("SELECT * FROM campaigns").fetchall():
        spec = plan_specs.get(c["id"])
        if not spec:
            continue
        if c["contract_type"] == "sponsorship":
            if c["sponsored_prescribers"] is None:
                n = len(eligible_prescribers(conn, c))
                conn.execute("UPDATE campaigns SET sponsored_prescribers=? WHERE id=?", (n, c["id"]))
            continue
        if c["booked_viewable"] is not None:
            continue
        start, end = date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"])
        delivered = conn.execute(
            "SELECT COALESCE(SUM(viewable),0) AS v FROM delivery_daily WHERE campaign_id=? AND traffic='valid'",
            (c["id"],),
        ).fetchone()["v"]
        total_days = (end - start).days + 1
        if start > t:  # scheduled: book from the expected weekly volume
            n = len(eligible_prescribers(conn, c))
            booked = int(n * total_days / 7 * 3 * spec["lam"] * 0.85 * 0.8)
        else:
            elapsed = min(total_days, (t - start).days)
            frac = max(elapsed / total_days, 1 / total_days)
            booked = delivered / max(0.05, (spec.get("pacing") or 1.0) * frac)
        booked = int(round(max(booked, 1000), -2))
        conn.execute(
            "UPDATE campaigns SET booked_viewable=?, budget=? WHERE id=?",
            (booked, round(booked / 1000 * (c["cpm"] or 0), 2), c["id"]),
        )
