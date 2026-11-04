"""Real Medicare Part D formulary data from CMS's quarterly public use file (SPUF).

What it does, in a background thread started when app/drugs/formulary.py loads:

1. Finds the newest quarterly SPUF zip through the data.gov catalog (CKAN API),
   or uses PARTD_ZIP_URL, or a known release (SPUF_2026_20260107.zip) as a fallback.
2. Reads only the members it needs from that multi-gigabyte zip with HTTP range
   requests: plan information, geographic locator, basic drugs formulary and
   beneficiary cost. The pharmacy network, pricing, insulin, excluded-drug and
   indication files are never fetched. Each needed member is itself a zip; it is
   copied (compressed, tens of MB) to DATA_DIR/partd/work-<pid>/ and read line by
   line straight out of that inner zip, so memory and disk use stay small. If the
   server refuses range requests and the whole file fits on disk, it downloads the
   whole zip instead.
3. Picks two real New Jersey plans (one standalone PDP, one MA-PD; see choose()),
   maps every knowledge-base generic to its RxNorm drug products through RxNav (at
   most 5 requests in flight, about 8 a second), and writes
   DATA_DIR/partd/formulary_subset.json with the two plans' formulary rows and a
   per-drug summary (lowest covered tier, PA/ST/QL).
4. Reuses that subset for 120 days; a failed refresh keeps the saved one.

Column names follow the CMS record layouts (2026 PUF and 2025 SPUF, identical for
the files used here, apart from SELECTED_DRUG_YN which 2026 adds):
  https://data.cms.gov/sites/default/files/2025-10/0564eb37-402d-4110-bd98-2d5399dc30e7/PUFRecordLayout-2026.pdf
  https://data.cms.gov/sites/default/files/2025-01/c299f432-d285-4ed9-9d06-0da14a389ef7/SPUFRecordLayout-2025.pdf
Files are pipe-delimited ASCII with a header row (SPUF methodology 2026); header
names are matched case-insensitively.

Nothing here blocks startup or a request; until the subset is ready the app uses
the labelled sample formulary.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - not Linux
    fcntl = None  # type: ignore[assignment]

import httpx

from ..config import settings
from . import net, rxnav
from .kb import kb

log = logging.getLogger("medscribe.partd")

CATALOG_API = ("https://catalog.data.gov/api/3/action/package_show"
               "?id=quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information")
FALLBACK_URL = "https://data.cms.gov/sites/default/files/2026-01/5942aa7e-a0c4-4e65-bd56-32608c33649f/SPUF_2026_20260107.zip"
LANDING_URL = ("https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/"
               "quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information")
LAYOUT_URL = "https://data.cms.gov/sites/default/files/2025-10/0564eb37-402d-4110-bd98-2d5399dc30e7/PUFRecordLayout-2026.pdf"
SPUF_RE = re.compile(r"SPUF[_.](20\d\d)[_.](\d{8})", re.I)
MAX_AGE_DAYS = 120
NEWER_CHECK_DAYS = 7  # how often a saved subset looks for a newer quarterly release
# data.gov's copy of the dataset can lag (its listing stopped at January 2026), so the
# catalog search and data.cms.gov's own catalog (DCAT data.json) are read too.
CATALOG_SEARCH = ("https://catalog.data.gov/api/3/action/package_search"
                  "?q=%22Quarterly%20Prescription%20Drug%20Plan%20Formulary%22&rows=20")
DCAT_URL = "https://data.cms.gov/data.json"
DCAT_MAX_CHARS = 150_000_000
STATE_NAME = os.environ.get("PARTD_STATE_NAME", "New Jersey")
STATE_CODE = os.environ.get("PARTD_STATE_CODE", "NJ")
SSA_STATE_PREFIX = os.environ.get("PARTD_SSA_STATE", "31")  # SSA state code for New Jersey (fallback only)
RETRY_AFTER_S = 6 * 3600
BUSY_WAIT_S = 600
BLOCK_SIZE = 4 * 1024 * 1024        # bytes per range request
RXNAV_RATE = 8.0                    # requests a second; NLM allows 20 per IP, the live app needs headroom
RXNAV_WORKERS = 5
RETRY_DELAYS = (15, 45, 90)         # seconds; together longer than net.BREAKER_S, so a tripped breaker is waited out
ABORT_AFTER = 15                    # consecutive RxNav failures before the rest of the mapping is skipped
MIN_FREE_BYTES = 150 * 1024 * 1024
SUBSET_VERSION = 1

# Which members of the package we read, by normalised file name.
KINDS = {
    "plan": re.compile(r"plan\s*info"),
    "geo": re.compile(r"geograph"),
    "formulary": re.compile(r"basic\s*drugs?\s*formulary"),
    "cost": re.compile(r"beneficiary\s*cost"),
}
NEVER = re.compile(r"pharmacy|pricing|insulin|excluded|indication")
TEXT_EXT = (".txt", ".csv", ".dat", ".psv")

# Tests inject httpx.MockTransport here.
transport: httpx.BaseTransport | None = None

_INITIAL = {"state": "sample", "detail": "Real CMS formulary not loaded yet; showing the sample formulary.",
            "release": None, "source_url": None, "started_at": None, "finished_at": None}
_status: dict = dict(_INITIAL)
_status_lock = threading.Lock()
_run_lock = threading.Lock()
_thread: threading.Thread | None = None


class PartDError(Exception):
    pass


class RangeUnsupported(PartDError):
    pass


class Busy(PartDError):
    """Another process holds the loader lock."""


# ---------------------------------------------------------------- status

def status() -> dict:
    with _status_lock:
        return dict(_status)


def _set(state: str, detail: str, **extra) -> None:
    with _status_lock:
        _status.update({"state": state, "detail": detail, **extra})
    log.info("Part D formulary: %s: %s", state, detail)


def _progress(detail: str, **extra) -> None:
    """Report progress. While a saved subset is still being served the state stays 'ready'."""
    if ready():
        _set("ready", f"Using the saved CMS subset; refreshing: {detail}", **extra)
    else:
        _set("loading", detail, **extra)


def reset() -> None:
    """Forget status and the cached subset (tests)."""
    with _status_lock:
        _status.clear()
        _status.update(_INITIAL)
    _cache.update({"key": None, "data": None})
    _discovered.update({"at": 0.0, "value": None})


def data_dir() -> Path:
    d = settings.data_dir / "partd"
    d.mkdir(parents=True, exist_ok=True)
    return d


def subset_path() -> Path:
    return data_dir() / "formulary_subset.json"


_cache: dict = {"key": None, "data": None}


def load_subset() -> dict | None:
    """The written subset, cached by path and mtime. None when there is none or it can't be read."""
    try:
        p = subset_path()
        key = (str(p), p.stat().st_mtime_ns)
    except OSError:
        return None
    if _cache["key"] != key:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        _cache.update({"key": key, "data": data})
    return _cache["data"]


def subset_age_days(subset: dict | None) -> float | None:
    if not subset or not subset.get("built_at"):
        return None
    try:
        built = datetime.fromisoformat(subset["built_at"])
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - built).total_seconds() / 86400


def ready() -> dict | None:
    """The subset when it is usable, else None."""
    s = load_subset()
    if s and s.get("version") == SUBSET_VERSION and s.get("plans") and s.get("drugs"):
        return s
    return None


def _ready_detail(s: dict, age: float | None = None) -> str:
    names = ", ".join(f"{p['name']} ({k})" for k, p in (s.get("plans") or {}).items())
    m = s.get("mapping") or {}
    when = f"; built {age:.0f} days ago" if age is not None else ""
    rel = f" ({s['release_text']})" if s.get("release_text") else ""
    return (f"CMS public use file, {s.get('release_label')}{rel}: {names}. "
            f"{m.get('mapped', '?')} of {m.get('of', '?')} medicines matched to RxNorm products{when}.")


# ---------------------------------------------------------------- background start

def start_background(force: bool = False) -> None:
    """Start the loader once per process. Never raises, never blocks."""
    try:
        _start(force)
    except Exception as exc:  # never fail startup
        log.warning("Part D loader not started: %s", exc)
        _set("failed", f"The CMS formulary loader could not start ({exc}); showing the sample formulary.")


def _autoload_off(force: bool) -> str | None:
    if net.offline():
        return "network lookups are switched off (DRUGS_OFFLINE=1)"
    if os.environ.get("PARTD_AUTOLOAD", "1") != "1":
        return "automatic loading is switched off (PARTD_AUTOLOAD=0)"
    if "pytest" in sys.modules and not force:
        return "test run"
    return None


def _start(force: bool) -> None:
    global _thread
    existing = ready()
    if existing:
        age = subset_age_days(existing)
        _set("ready", _ready_detail(existing, age), release=existing.get("release_label"),
             source_url=existing.get("source_url"), finished_at=existing.get("built_at"))
        if age is not None and age < MAX_AGE_DAYS and not force:
            if _autoload_off(False) is None and _check_due() and not (_thread is not None and _thread.is_alive()):
                _thread = threading.Thread(target=_check_newer, name="partd-check", daemon=True)
                _thread.start()
            return
    off = _autoload_off(force)
    if off:
        if not existing:
            _set("sample", f"Real CMS formulary not loaded: {off}. Showing the sample formulary.")
        return
    if _thread is not None and _thread.is_alive():
        return
    _progress("Starting the CMS Part D formulary loader…", started_at=_now(), finished_at=None)
    _thread = threading.Thread(target=_loop, name="partd-loader", daemon=True)
    _thread.start()


def _loop() -> None:
    while True:
        try:
            run()
            return
        except Busy as exc:
            _progress(str(exc))
            time.sleep(BUSY_WAIT_S)
            s = ready()
            age = subset_age_days(s)
            if s and age is not None and age < MAX_AGE_DAYS:
                _set("ready", _ready_detail(s, age), release=s.get("release_label"), source_url=s.get("source_url"))
                return
        except Exception as exc:  # never let the thread die silently; run() already set the status
            log.warning("Part D loader failed: %s", exc)
            time.sleep(RETRY_AFTER_S)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- HTTP

def _client() -> httpx.Client:
    kw: dict = {"timeout": httpx.Timeout(30.0, read=180.0), "follow_redirects": True,
                "headers": {"User-Agent": "CustomAi-MedScribe-demo/0.1 (Part D formulary loader)"}}
    if transport is not None:
        kw["transport"] = transport
    return httpx.Client(**kw)


LINK_RE = re.compile(r"""(?:https?://[^"'\s<>]+)?/sites/default/files/[^"'\s<>]*?SPUF[_.]20\d\d[_.]\d{8}[^"'\s<>/]*\.zip""", re.I)


SPUF_URL_RE = re.compile(r"""https?://[^"'\s<>]+?SPUF[_.]20\d\d[_.]\d{8}[^"'\s<>/]*\.zip""", re.I)
_discovered: dict = {"at": 0.0, "value": None}


def _ckan_candidates(client: httpx.Client) -> list[str]:
    """SPUF zip URLs from data.gov: the dataset record, and every record a title search finds."""
    urls: list[str] = []
    for api in (CATALOG_API, CATALOG_SEARCH):
        try:
            r = client.get(api, timeout=30.0)
            r.raise_for_status()
            result = (r.json() or {}).get("result") or {}
            packages = result.get("results") if isinstance(result.get("results"), list) else [result]
            for pkg in packages:
                for res in (pkg or {}).get("resources") or []:
                    url = (res or {}).get("url") or ""
                    if url and SPUF_RE.search(url) and url.lower().split("?")[0].endswith(".zip"):
                        urls.append(url)
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            log.info("catalog lookup failed (%s): %s", api.split("?")[0], exc)
    return urls


def _dcat_candidates(client: httpx.Client) -> list[str]:
    """SPUF zip URLs listed in data.cms.gov's own catalog, read as a stream."""
    urls: list[str] = []
    tail, seen = "", 0
    try:
        with client.stream("GET", DCAT_URL, timeout=httpx.Timeout(30.0, read=120.0)) as r:
            if r.status_code != 200:
                log.info("data.cms.gov catalog answered HTTP %s", r.status_code)
                return []
            for chunk in r.iter_text():
                seen += len(chunk)
                text = (tail + chunk).replace("\\/", "/")
                urls += SPUF_URL_RE.findall(text)
                tail = text[-500:]
                if seen > DCAT_MAX_CHARS:
                    break
    except httpx.HTTPError as exc:
        log.info("data.cms.gov catalog lookup failed: %s", exc)
    return urls


def _release_key(url: str) -> tuple[int, str]:
    m = SPUF_RE.search(url)
    return (int(m.group(1)), m.group(2)) if m else (0, "")


def pick_newest(urls: list[str], today: date | None = None) -> str | None:
    """The newest release for a contract year already in effect (a file for next year can appear
    in December; it describes plans that have not started yet)."""
    today = today or date.today()
    uniq = list(dict.fromkeys(u for u in urls if SPUF_RE.search(u)))
    if not uniq:
        return None
    current = [u for u in uniq if _release_key(u)[0] <= today.year]
    return max(current or uniq, key=_release_key)


def discover(client: httpx.Client) -> tuple[str, str]:
    """(zip URL, how it was found): PARTD_ZIP_URL, else the newest release listed by data.gov or
    data.cms.gov, else links on the CMS landing page, else a known release."""
    override = os.environ.get("PARTD_ZIP_URL", "").strip()
    if override:
        return override, "PARTD_ZIP_URL"
    if _discovered["value"] and time.monotonic() - _discovered["at"] < 600:
        return _discovered["value"]
    ckan = _ckan_candidates(client)
    dcat = _dcat_candidates(client)
    best = pick_newest(ckan + dcat)
    if best:
        n = len(set(ckan + dcat))
        where = "data.cms.gov catalog" if best in dcat and best not in ckan else "data.gov catalog"
        value = (best, f"{where} (newest of {n} quarterly releases listed)")
        _discovered.update({"at": time.monotonic(), "value": value})
        return value
    try:
        r = client.get(LANDING_URL, timeout=30.0)
        if r.status_code == 200:
            found = []
            for link in LINK_RE.findall(r.text.replace("\\/", "/")):
                url = link if link.lower().startswith("http") else "https://data.cms.gov" + link
                if SPUF_RE.search(url):
                    found.append(url)
            best = pick_newest(found)
            if best:
                return best, "CMS landing page (newest quarterly release)"
    except httpx.HTTPError as exc:
        log.info("landing page lookup failed: %s", exc)
    return FALLBACK_URL, "known release (catalog unavailable)"


# ---------------------------------------------------------------- newer-release check

def _check_marker() -> Path:
    return data_dir() / "newest_check.json"


def _check_due() -> bool:
    try:
        data = json.loads(_check_marker().read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(data["checked_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return (datetime.now(timezone.utc) - checked).total_seconds() > NEWER_CHECK_DAYS * 86400


def _mark_checked(url: str, how: str) -> None:
    try:
        _check_marker().write_text(json.dumps({"checked_at": _now(), "newest": url, "found_via": how}), encoding="utf-8")
    except OSError as exc:
        log.info("could not save the newer-release check: %s", exc)


def _check_newer() -> None:
    """Background: if CMS lists a newer release than the saved subset, rebuild from it."""
    try:
        with _client() as client:
            url, how = discover(client)
        _mark_checked(url, how)
        newest, _ = release_from_url(url)
        current = (ready() or {}).get("release_date")
        if newest and current and newest > current:
            log.info("Part D: newer release %s found (saved subset is %s); rebuilding", newest, current)
            _loop()
    except Exception as exc:  # noqa: BLE001 - a failed check keeps the saved subset
        log.info("Part D newer-release check failed: %s", exc)


def release_from_url(url: str) -> tuple[str | None, int | None]:
    """('2026-01-07', 2026) from a SPUF file name."""
    m = SPUF_RE.search(url)
    if not m:
        return None, None
    d = m.group(2)
    try:
        date(int(d[:4]), int(d[4:6]), int(d[6:]))
    except ValueError:
        return None, int(m.group(1))
    return f"{d[:4]}-{d[4:6]}-{d[6:]}", int(m.group(1))


def release_label(release_date: str | None, year: int | None = None) -> str:
    """'Q1 2026': the quarter of the release date."""
    if not release_date:
        return f"contract year {year}" if year else "latest quarterly release"
    d = date.fromisoformat(release_date)
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


def release_text(release_date: str | None, year: int | None = None) -> str:
    """'released 7 Jan 2026, contract year 2026'."""
    if not release_date:
        return ""
    d = date.fromisoformat(release_date)
    return f"released {d.day} {d.strftime('%b %Y')}" + (f", contract year {year}" if year else "")


def probe(client: httpx.Client, url: str) -> tuple[int | None, bool]:
    """(size in bytes, supports ranges)."""
    with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as r:
        if r.status_code == 206:
            cr = r.headers.get("content-range", "")
            m = re.search(r"/(\d+)\s*$", cr)
            return (int(m.group(1)) if m else None), True
        if r.status_code == 200:
            cl = r.headers.get("content-length")
            return (int(cl) if cl and cl.isdigit() else None), False
        raise PartDError(f"The CMS file server answered HTTP {r.status_code} for {url}.")


class RangeReader(io.RawIOBase):
    """A seekable, read-only view of a remote file through HTTP range requests."""

    def __init__(self, client: httpx.Client, url: str, size: int, block: int | None = None, keep: int = 6):
        self.client, self.url, self.size, self.keep = client, url, size, keep
        self.block = block or BLOCK_SIZE
        self.pos = 0
        self.blocks: OrderedDict[int, bytes] = OrderedDict()
        self.fetched = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        elif whence == io.SEEK_END:
            self.pos = self.size + offset
        self.pos = max(0, self.pos)
        return self.pos

    def _get(self, idx: int) -> bytes:
        if idx in self.blocks:
            self.blocks.move_to_end(idx)
            return self.blocks[idx]
        start = idx * self.block
        end = min(self.size, start + self.block) - 1
        last: Exception | None = None
        for attempt in range(5):
            try:
                r = self.client.get(self.url, headers={"Range": f"bytes={start}-{end}"})
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(min(20, 2 ** attempt))
                continue
            if r.status_code == 206:
                data = r.content
                break
            if r.status_code == 200:
                raise RangeUnsupported("The server ignored the range request.")
            if r.status_code in (408, 429, 500, 502, 503, 504):
                last = PartDError(f"HTTP {r.status_code}")
                time.sleep(min(20, 2 ** attempt))
                continue
            raise PartDError(f"The CMS file server answered HTTP {r.status_code} to a range request.")
        else:
            raise PartDError(f"Could not read the CMS file ({last}).")
        self.fetched += len(data)
        self.blocks[idx] = data
        while len(self.blocks) > self.keep:
            self.blocks.popitem(last=False)
        return data

    def readinto(self, b) -> int:  # type: ignore[override]
        if self.pos >= self.size:
            return 0
        idx = self.pos // self.block
        blk = self._get(idx)
        off = self.pos - idx * self.block
        n = min(len(b), len(blk) - off)
        if n <= 0:
            return 0
        b[:n] = blk[off:off + n]
        self.pos += n
        return n


def download_whole(client: httpx.Client, url: str, size: int | None, dest: Path) -> None:
    free = shutil.disk_usage(dest.parent).free
    if size is None or size + MIN_FREE_BYTES > free:
        raise PartDError("The CMS server does not allow partial downloads, and the whole file "
                         f"({(size or 0) / 1e9:.1f} GB) does not fit on this server's disk.")
    tmp = dest.with_suffix(".part")
    with client.stream("GET", url) as r, open(tmp, "wb") as fh:
        if r.status_code != 200:
            raise PartDError(f"The CMS file server answered HTTP {r.status_code}.")
        for chunk in r.iter_bytes(1 << 20):
            fh.write(chunk)
    tmp.replace(dest)


# ---------------------------------------------------------------- package members

Source = tuple  # (path, member name inside that zip, or None for a plain text file)


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", name.lower())).strip()


def classify(name: str) -> str | None:
    base = _norm_name(name.rsplit("/", 1)[-1])
    if NEVER.search(base):
        return None
    for kind, rx in KINDS.items():
        if rx.search(base):
            return kind
    return None


def _is_text(name: str) -> bool:
    base = name.rsplit("/", 1)[-1].lower()
    return base.endswith(TEXT_EXT) or "." not in base


def _ensure_space(where: Path, need: int) -> None:
    free = shutil.disk_usage(where).free
    if need + MIN_FREE_BYTES > free:
        raise PartDError(f"Not enough disk space to copy the CMS files ({need / 1e6:.0f} MB needed, {free / 1e6:.0f} MB free).")


def extract_members(outer: zipfile.ZipFile, workdir: Path) -> dict[str, list[Source]]:
    """Sources for each needed kind, in package order (a kind can come in several parts).

    A nested zip is copied to workdir as is and read in place later; a text member stored
    directly in the outer zip is copied out."""
    out: dict[str, list[Source]] = {}
    seen: list[str] = []
    for n, info in enumerate(outer.infolist()):
        if info.is_dir():
            continue
        seen.append(info.filename)
        kind = classify(info.filename)
        if not kind:
            continue
        if info.filename.lower().endswith(".zip"):
            _ensure_space(workdir, info.file_size)
            local = workdir / f"{kind}-{n}.zip"
            with outer.open(info) as src, open(local, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            with zipfile.ZipFile(local) as inner:
                members = sorted(i.filename for i in inner.infolist() if not i.is_dir() and _is_text(i.filename))
            if not members:
                local.unlink(missing_ok=True)
                continue
            out.setdefault(kind, []).extend((local, m) for m in members)
        elif _is_text(info.filename):
            _ensure_space(workdir, info.file_size)
            target = workdir / f"{kind}-{n}.txt"
            with outer.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            out.setdefault(kind, []).append((target, None))
    missing = [k for k in ("plan", "geo", "formulary") if k not in out]
    if missing:
        raise PartDError(f"The CMS package did not contain the expected files ({', '.join(missing)}). "
                         f"Members seen: {', '.join(seen[:12])}")
    return out


@contextmanager
def _open_text(src: Source):
    path, member = src
    if member is None:
        with open(path, "r", encoding="latin-1", newline="") as fh:
            yield fh
    else:
        with zipfile.ZipFile(path) as zf, io.TextIOWrapper(zf.open(member), encoding="latin-1", newline="") as fh:
            yield fh


def _sources(src) -> list[Source]:
    if src is None:
        return []
    if isinstance(src, (str, Path)):
        return [(Path(src), None)]
    if isinstance(src, tuple):
        return [src]
    return [s if isinstance(s, tuple) else (Path(s), None) for s in src]


def rows(src: Source | Path | Iterable) -> Iterator[dict]:
    """Pipe-delimited rows as dicts keyed by upper-case header names; each part has its own header."""
    for s in _sources(src):
        with _open_text(s) as fh:
            reader = csv.reader(fh, delimiter="|", quoting=csv.QUOTE_NONE)
            header = None
            for rec in reader:
                if not rec or (len(rec) == 1 and not rec[0].strip()):
                    continue
                if header is None:
                    header = [h.replace("﻿", "").strip().upper() for h in rec]
                    continue
                yield {header[i]: (rec[i].strip() if i < len(rec) else "") for i in range(len(header))}


def _code(x: str | None) -> str:
    x = (x or "").strip()
    return str(int(x)) if x.isdigit() else x.upper()


def _yes(x: str | None) -> bool:
    return (x or "").strip().upper() in ("Y", "1", "YES", "TRUE")


def _money(x: str | None) -> float | None:
    try:
        return round(float((x or "").replace("$", "").replace(",", "")), 2)
    except ValueError:
        return None


def _qty(x: str) -> str:
    try:
        f = float(x)
    except ValueError:
        return x
    return str(int(f)) if f.is_integer() else f"{f:g}"


# ---------------------------------------------------------------- geography and plans

def state_geography(geo_src) -> dict:
    counties, pdp_regions, ma_regions, names = set(), set(), set(), {}
    for r in rows(geo_src):
        sname = (r.get("STATENAME") or r.get("STATE_NAME") or r.get("STATE") or "").strip().lower()
        raw_cc = (r.get("COUNTY_CODE") or "").strip()
        in_state = sname in (STATE_NAME.lower(), STATE_CODE.lower()) or (not sname and raw_cc.zfill(5).startswith(SSA_STATE_PREFIX))
        if not in_state:
            continue
        if raw_cc:
            cc = _code(raw_cc)
            counties.add(cc)
            names[cc] = r.get("COUNTY") or ""
        if r.get("PDP_REGION_CODE"):
            pdp_regions.add(_code(r["PDP_REGION_CODE"]))
        if r.get("MA_REGION_CODE"):
            ma_regions.add(_code(r["MA_REGION_CODE"]))
    if not counties and not pdp_regions:
        raise PartDError(f"The geographic locator file has no rows for {STATE_NAME}.")
    return {"counties": counties, "pdp_regions": pdp_regions, "ma_regions": ma_regions, "county_names": names}


def candidate_plans(plan_src, geo: dict) -> tuple[dict, dict]:
    """(PDPs, MA-PDs) offered in the state, keyed by contract-plan-segment. SNPs and suppressed plans excluded.

    A standalone PDP (S contract) qualifies when its PDP region is the state's; an MA-PD
    (H or R contract) when it lists a county in the state, or the state itself."""
    pdps: dict[str, dict] = {}
    mapds: dict[str, dict] = {}
    for r in rows(plan_src):
        contract = (r.get("CONTRACT_ID") or "").strip().upper()
        if not contract or _yes(r.get("PLAN_SUPPRESSED_YN")):
            continue
        if (r.get("SNP") or "").strip().upper() not in ("", "0", "N", "NO"):
            continue  # special needs plans (1 chronic, 2 dual, 3 institutional) serve only their members
        plan_id = (r.get("PLAN_ID") or "").strip().zfill(3)
        seg = ((r.get("SEGMENT_ID") or "0").strip() or "0").zfill(3)
        fid = (r.get("FORMULARY_ID") or "").strip()
        if not fid:
            continue
        key = f"{contract}-{plan_id}-{seg}"
        base = {"contract_id": contract, "plan_id": plan_id, "segment_id": seg, "formulary_id": fid,
                "contract_name": (r.get("CONTRACT_NAME") or "").strip(), "plan_name": (r.get("PLAN_NAME") or "").strip(),
                "premium": _money(r.get("PREMIUM")), "deductible": _money(r.get("DEDUCTIBLE"))}
        if contract.startswith("S"):
            if _code(r.get("PDP_REGION_CODE")) in geo["pdp_regions"]:
                pdps.setdefault(key, {**base, "type": "PDP", "region": _code(r.get("PDP_REGION_CODE"))})
        elif contract[:1] in ("H", "R"):
            cc = _code(r.get("COUNTY_CODE"))
            st = (r.get("STATE") or "").strip().upper()
            in_county = bool(cc) and cc in geo["counties"]
            in_region = contract.startswith("R") and not cc and _code(r.get("MA_REGION_CODE")) in geo["ma_regions"]
            if in_county or st in (STATE_CODE.upper(), STATE_NAME.upper()) or in_region:
                p = mapds.setdefault(key, {**base, "type": "MA-PD", "counties": set()})
                if in_county:
                    p["counties"].add(cc)
    if any(p["counties"] for p in mapds.values()):
        # the file lists counties: keep only plans that serve at least one county in the state
        mapds = {k: p for k, p in mapds.items() if p["counties"]}
    return pdps, mapds


# ---------------------------------------------------------------- RxNorm mapping

def _components(name: str, tty: str) -> list[str]:
    """Ingredient components of an RxNorm product name; inert pack items dropped."""
    n = re.sub(r"\s*\[[^\]]*\]\s*$", "", name or "")  # drop "[Brand]"
    if tty in ("GPCK", "BPCK") or n.startswith("{"):
        parts = []
        for grp in re.findall(r"\(([^()]*)\)", n):
            parts += [p.strip() for p in grp.split(" / ")]
        return [p for p in parts if p and not re.search(r"inert|placebo", p, re.I)]
    return [p.strip() for p in n.split(" / ") if p.strip()]


def _matches(component: str, names: Iterable[str]) -> bool:
    c = component.lower()
    return any(re.search(rf"(?<![a-z]){re.escape(x.lower())}(?![a-z])", c) for x in names if x)


class _RateLimiter:
    """Spaces calls across threads to RXNAV_RATE a second."""

    def __init__(self) -> None:
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        rate = RXNAV_RATE
        if rate <= 0:
            return
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + 1.0 / rate
        if at > now:
            time.sleep(at - now)


_limiter = _RateLimiter()


def _retrying(fn: Callable, *args, tries: int = 4):
    """Call an RxNav function; on SourceUnavailable wait (RETRY_DELAYS) and try again."""
    for i in range(tries):
        if net.offline():
            return fn(*args)  # raises SourceUnavailable at once
        _limiter.wait()
        try:
            return fn(*args)
        except net.SourceUnavailable:
            if i == tries - 1:
                raise
            time.sleep(RETRY_DELAYS[min(i, len(RETRY_DELAYS) - 1)])
    return None


def _pick_ingredient(ings: list[dict], name: str) -> dict | None:
    named = [i for i in ings if i.get("rxcui") and i.get("name")]
    best = next((i for i in named if i["name"].lower() in name.lower()), named[0] if len(named) == 1 else None)
    return {"rxcui": best["rxcui"], "name": best["name"], "tty": best.get("tty")} if best else None


def resolve_ingredient(name: str, aliases: list[str]) -> dict | None:
    """{rxcui, name, tty} for an ingredient (IN, PIN or MIN), or None. Verified with properties.json."""
    for n in [name, *aliases]:
        rx = _retrying(rxnav.rxcui_by_name, n)
        if not rx:
            continue
        props = _retrying(rxnav.properties, rx) or {}
        tty = props.get("tty")
        if tty in (None, "IN", "PIN", "MIN"):
            return {"rxcui": rx, "name": props.get("name") or n, "tty": tty}
        best = _pick_ingredient(_retrying(rxnav.ingredients, rx) or [], name)
        if best:
            return best
    cands = _retrying(rxnav.approximate_term, name, 3) or []
    for c in cands[:1]:
        best = _pick_ingredient(_retrying(rxnav.ingredients, c["rxcui"]) or [], name)
        if best:
            return best
    return None


def map_generic(generic: str) -> dict:
    """RxNorm products for one KB generic: {generic, ingredients: [...], products: {rxcui: {name, tty}}, error}.

    A product counts only when every one of its ingredients is one of the generic's, and
    every one of the generic's ingredients is in it: lisinopril does not pick up
    lisinopril/hydrochlorothiazide, and amoxicillin-clavulanate picks up only the combination."""
    d = kb().get(generic)
    if d is None:
        return {"generic": generic, "ingredients": [], "products": {}, "error": "unknown generic"}
    parts = d.ingredients if len(d.ingredients) > 1 else [generic]
    resolved = []
    for p in parts:
        sub = kb().get(p)
        r = resolve_ingredient(p, (sub.aliases if sub else (d.aliases if p == generic else [])) or [])
        if not r:
            return {"generic": generic, "ingredients": resolved, "products": {}, "error": f"no RxNorm ingredient for {p}"}
        resolved.append(r)
    names = {r["name"] for r in resolved} | set(parts)
    per_part = [{r["name"], p} for r, p in zip(resolved, parts)]

    def collect(rxcui: str) -> dict[str, dict]:
        found: dict[str, dict] = {}
        for prod in _retrying(rxnav.related_products, rxcui) or []:
            comps = _components(prod["name"], prod["tty"])
            if not comps:
                continue
            if not all(_matches(c, names) for c in comps):
                continue
            if not all(any(_matches(c, ns) for c in comps) for ns in per_part):
                continue
            found[prod["rxcui"]] = {"name": prod["name"], "tty": prod["tty"]}
        return found

    products = collect(resolved[0]["rxcui"])
    if not products:
        # A precise ingredient (a salt) may not relate to products directly: go through its base ingredient.
        for ing in _retrying(rxnav.ingredients, resolved[0]["rxcui"]) or []:
            if ing.get("rxcui") and ing["rxcui"] != resolved[0]["rxcui"]:
                products = collect(ing["rxcui"])
                if products:
                    break
    return {"generic": generic, "ingredients": resolved, "products": products, "error": None if products else "no products"}


_unavailable = {"run": 0}
_unavailable_lock = threading.Lock()


def _safe_map(generic: str) -> dict:
    with _unavailable_lock:
        if _unavailable["run"] >= ABORT_AFTER:
            return {"generic": generic, "ingredients": [], "products": {}, "error": "RxNav unavailable"}
    try:
        res = map_generic(generic)
        with _unavailable_lock:
            _unavailable["run"] = 0
        return res
    except net.SourceUnavailable:
        with _unavailable_lock:
            _unavailable["run"] += 1
        return {"generic": generic, "ingredients": [], "products": {}, "error": "RxNav unavailable"}


def map_all(generics: list[str], workers: int | None = None) -> dict[str, dict]:
    with _unavailable_lock:
        _unavailable["run"] = 0
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers or RXNAV_WORKERS) as pool:
        for res in pool.map(_safe_map, generics):
            out[res["generic"]] = res
    return out


# ---------------------------------------------------------------- formulary scan

def scan_coverage(form_src, formularies: set[str], product_index: dict[str, set[str]]) -> dict[str, set[str]]:
    """{formulary_id: generics with at least one covered product}."""
    cov: dict[str, set[str]] = {f: set() for f in formularies}
    for r in rows(form_src):
        fid = r.get("FORMULARY_ID", "")
        if fid not in cov:
            continue
        gens = product_index.get(r.get("RXCUI", ""))
        if gens:
            cov[fid] |= gens
    return cov


def extract_rows(form_src, formularies: set[str]) -> dict[str, dict[str, list]]:
    """{formulary_id: {rxcui: [tier, pa, st, ql, ql_amount, ql_days]}}; per RXCUI the lowest tier, then fewest restrictions."""
    out: dict[str, dict[str, list]] = {f: {} for f in formularies}
    for r in rows(form_src):
        fid = r.get("FORMULARY_ID", "")
        if fid not in out:
            continue
        rx = r.get("RXCUI", "")
        if not rx:
            continue
        try:
            tier = int(float(r.get("TIER_LEVEL_VALUE") or 0))
        except ValueError:
            tier = 0
        row = [tier, _yes(r.get("PRIOR_AUTHORIZATION_YN")), _yes(r.get("STEP_THERAPY_YN")), _yes(r.get("QUANTITY_LIMIT_YN")),
               (r.get("QUANTITY_LIMIT_AMOUNT") or "").strip(), (r.get("QUANTITY_LIMIT_DAYS") or "").strip()]
        prev = out[fid].get(rx)
        if prev is None or (row[0], row[1], row[2], row[3]) < (prev[0], prev[1], prev[2], prev[3]):
            out[fid][rx] = row
    return out


def specialty_tiers(cost_src, plans: list[dict]) -> dict[str, list[int]]:
    """Tiers the beneficiary cost file marks TIER_SPECIALTY_YN for each chosen plan."""
    keys = {(p["contract_id"], p["plan_id"], p["segment_id"]): p["plan_key"] for p in plans}
    out: dict[str, set[int]] = {p["plan_key"]: set() for p in plans}
    for r in rows(cost_src):
        key = ((r.get("CONTRACT_ID") or "").strip().upper(), (r.get("PLAN_ID") or "").strip().zfill(3),
               ((r.get("SEGMENT_ID") or "0").strip() or "0").zfill(3))
        pk = keys.get(key)
        if pk and _yes(r.get("TIER_SPECIALTY_YN")):
            try:
                out[pk].add(int(float(r.get("TIER") or 0)))
            except ValueError:
                pass
    return {k: sorted(v) for k, v in out.items()}


def choose(cands: dict[str, dict], cov: dict[str, set[str]], n_meds: int) -> tuple[dict | None, str]:
    """The plan whose formulary covers the most knowledge-base medicines; ties go to more
    counties served, then the lower premium, then contract, plan and segment ID."""
    if not cands:
        return None, "no candidate plans"

    def covered(p) -> int:
        return len(cov.get(p["formulary_id"], ()))

    def score(p):
        return (-covered(p), -len(p.get("counties") or ()), p["premium"] if p["premium"] is not None else 1e9,
                p["contract_id"], p["plan_id"], p["segment_id"])

    ranked = sorted(cands.values(), key=score)
    best = ranked[0]
    n = covered(best)
    tied = sum(1 for p in ranked if covered(p) == n)
    why = f"Covers {n} of {n_meds} knowledge-base medicines, the most of the {len(cands)} {best['type']} plans in {STATE_NAME}"
    if tied > 1:
        why += f" ({tied} tied; ties go to more counties served, then the lower premium, then contract ID)"
    if best["premium"] is not None:
        why += f"; premium ${best['premium']:.2f} a month"
    if best.get("counties"):
        why += f"; serves {len(best['counties'])} {STATE_NAME} counties"
    return best, why + "."


# ---------------------------------------------------------------- per-drug summary

def summarise(drugs_map: dict[str, dict], plan_rows: dict[str, list], max_tier: int, spec: list[int]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for generic, m in drugs_map.items():
        if not m.get("products"):
            out[generic] = {"mapped": False}
            continue
        covered = []
        for rx, prod in m["products"].items():
            row = plan_rows.get(rx)
            if row:
                covered.append((row, rx, prod))
        if not covered:
            out[generic] = {"mapped": True, "covered": False}
            continue
        covered.sort(key=lambda t: (t[0][0], t[0][1], t[0][2], t[0][3], t[2]["tty"] not in ("SCD", "GPCK"), t[2]["name"]))
        row, rx, prod = covered[0]
        tier = row[0]
        out[generic] = {
            "mapped": True, "covered": True, "tier": tier,
            "tier_text": f"Tier {tier} of {max_tier}" + (" (specialty)" if tier in spec else ""),
            "specialty": tier in spec,
            "pa": row[1], "st": row[2], "ql": row[3],
            "ql_detail": (f"{_qty(row[4])} per {_qty(row[5])} days" if row[3] and row[4] and row[5] else ("quantity limit" if row[3] else None)),
            "generic_product": any(p["tty"] in ("SCD", "GPCK") for _r, _x, p in covered),
            "best_rxcui": rx,
            "products": [p["name"] for _r, _x, p in covered[:4]],
            "covered_products": len(covered),
        }
    return out


# ---------------------------------------------------------------- the whole run

def _file_lock():
    """Exclusive lock on DATA_DIR/partd/loader.lock: the open file, None where unsupported, False when held elsewhere."""
    if fcntl is None:
        return None
    fh = open(data_dir() / "loader.lock", "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    return fh


def run(generics: list[str] | None = None, client: httpx.Client | None = None) -> dict:
    """Build the subset synchronously (the background thread calls this). Returns the subset."""
    if net.offline():
        msg = "Network lookups are switched off (DRUGS_OFFLINE=1), so the CMS file can't be read."
        if not ready():
            _set("failed", msg + " Showing the sample formulary.", finished_at=_now())
        raise PartDError(msg)
    with _run_lock:
        lock = _file_lock()
        if lock is False:
            raise Busy("Another server process is building the CMS formulary subset; waiting for it.")
        try:
            return _build(generics, client)
        finally:
            if lock is not None:
                lock.close()


def _read_members(client: httpx.Client, url: str, size: int | None, ranged: bool, workdir: Path) -> tuple[dict, int | None]:
    if ranged and not size:
        raise PartDError("The CMS file server did not report the file size.")
    if ranged:
        raw = RangeReader(client, url, size)
        try:
            with zipfile.ZipFile(io.BufferedReader(raw, buffer_size=1 << 20)) as outer:
                return extract_members(outer, workdir), raw.fetched
        except RangeUnsupported:
            log.info("range requests stopped working part way; trying a whole download")
    local = workdir / "spuf.zip"
    _progress(f"Downloading the whole CMS file ({(size or 0) / 1e9:.1f} GB; the server does not allow partial reads)…")
    download_whole(client, url, size, local)
    try:
        with zipfile.ZipFile(local) as outer:
            files = extract_members(outer, workdir)
    finally:
        local.unlink(missing_ok=True)
    return files, size


def _build(generics: list[str] | None, client: httpx.Client | None) -> dict:
    _progress("Finding the newest CMS Part D public use file…", started_at=_now(), finished_at=None)
    own = client is None
    client = client or _client()
    base = data_dir()
    for stale in base.glob("work-*"):  # left by a crashed run; we hold the lock
        shutil.rmtree(stale, ignore_errors=True)
    workdir = base / f"work-{os.getpid()}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        url, how = discover(client)
        _mark_checked(url, how)
        rel, year = release_from_url(url)
        label, text = release_label(rel, year), release_text(rel, year)
        _progress(f"Reading the CMS file ({label}) with range requests…", release=label, source_url=url)
        try:
            size, ranged = probe(client, url)
            files, fetched = _read_members(client, url, size, ranged, workdir)
        except zipfile.BadZipFile as exc:
            raise PartDError(f"The CMS file is not a readable zip ({exc}).") from exc
        except httpx.HTTPError as exc:
            raise PartDError(f"Could not reach the CMS file server ({type(exc).__name__}: {exc}).") from exc

        geo = state_geography(files["geo"])
        pdps, mapds = candidate_plans(files["plan"], geo)
        if not pdps and not mapds:
            raise PartDError(f"No {STATE_NAME} plans found in the plan information file.")

        gens = list(generics) if generics is not None else sorted(kb().drugs)
        _progress(f"Matching {len(gens)} medicines to RxNorm drug products (RxNav)…")
        mapping = map_all(gens)
        mapped = [g for g in gens if mapping[g].get("products")]
        if len(mapped) < max(1, int(0.6 * len(gens))):
            raise PartDError(f"Only {len(mapped)} of {len(gens)} medicines could be matched to RxNorm products "
                             "(RxNav unreachable or answering slowly).")
        product_index: dict[str, set[str]] = {}
        for g in mapped:
            for rx in mapping[g]["products"]:
                product_index.setdefault(rx, set()).add(g)

        _progress("Scanning the basic drugs formulary file…")
        fids = {p["formulary_id"] for p in [*pdps.values(), *mapds.values()]}
        cov = scan_coverage(files["formulary"], fids, product_index)
        pdp, pdp_why = choose(pdps, cov, len(gens))
        mapd, mapd_why = choose(mapds, cov, len(gens))
        chosen = []
        if pdp:
            chosen.append({**pdp, "plan_key": "PDP-A", "why": pdp_why})
        if mapd:
            chosen.append({**{k: v for k, v in mapd.items() if k != "counties"}, "plan_key": "MAPD-B",
                           "why": mapd_why, "counties_served": len(mapd.get("counties") or ())})
        plan_rows = extract_rows(files["formulary"], {p["formulary_id"] for p in chosen})
        spec = specialty_tiers(files.get("cost"), chosen)

        plans_out, rows_out, drugs_out = {}, {}, {}
        for p in chosen:
            pk = p["plan_key"]
            rws = plan_rows.get(p["formulary_id"], {})
            max_tier = max((r[0] for r in rws.values()), default=0)
            name = p["plan_name"] or p["contract_name"] or f"{p['contract_id']}-{p['plan_id']}"
            plans_out[pk] = {
                "plan_key": pk, "name": name, "contract_name": p["contract_name"], "type": p["type"],
                "contract_id": p["contract_id"], "plan_id": p["plan_id"], "segment_id": p["segment_id"],
                "formulary_id": p["formulary_id"], "premium": p["premium"], "deductible": p["deductible"],
                "region": p.get("region"), "counties_served": p.get("counties_served"), "year": year,
                "max_tier": max_tier, "specialty_tiers": spec.get(pk, []), "why": p["why"],
                "source": f"Medicare Part D formulary: CMS public use file, {label}",
            }
            rows_out[pk] = rws
            drugs_out[pk] = summarise({g: mapping[g] for g in gens}, rws, max_tier, spec.get(pk, []))

        subset = {
            "version": SUBSET_VERSION, "built_at": _now(), "source_url": url, "found_via": how, "landing_url": LANDING_URL,
            "layout_url": LAYOUT_URL, "release_date": rel, "contract_year": year, "release_label": label,
            "release_text": text, "state": STATE_CODE, "plans": plans_out, "rows": rows_out, "drugs": drugs_out,
            "selection": {"pdp_candidates": len(pdps), "mapd_candidates": len(mapds),
                          "rule": "Most knowledge-base medicines covered, then more counties served, then lowest premium, "
                                  "then contract ID; special needs plans and suppressed plans excluded."},
            "mapping": {"mapped": len(mapped), "of": len(gens),
                        "unmapped": sorted(g for g in gens if not mapping[g].get("products"))[:80]},
            "bytes_read": fetched,
        }
        tmp = subset_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(subset, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(subset_path())
        _set("ready", _ready_detail(subset, 0.0), release=label, source_url=url, finished_at=_now())
        return subset
    except Exception as exc:
        if ready():
            _set("ready", f"Refresh failed ({exc}); still using the saved CMS subset.", finished_at=_now())
        else:
            _set("failed", f"{exc} Showing the sample formulary; the loader tries again every "
                           f"{RETRY_AFTER_S // 3600} hours.", finished_at=_now())
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if own:
            client.close()
