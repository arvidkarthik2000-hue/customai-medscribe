"""Settings, read once from environment variables.

Secrets (ASSEMBLYAI_API_KEY, ANTHROPIC_API_KEY, APP_SECRET, DEMO_ACCESS_CODE) are
read here and nowhere else, and are never logged or returned by any endpoint.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def build_id() -> str:
    """This deploy's id (Render sets RENDER_GIT_COMMIT), so cached checks rerun after a deploy."""
    return (_env("RENDER_GIT_COMMIT") or _env("BUILD_ID") or "local")[:12]


@dataclass(frozen=True)
class Settings:
    # secrets
    assemblyai_api_key: str = field(default_factory=lambda: _env("ASSEMBLYAI_API_KEY"))
    # MEDSCRIBE_ANTHROPIC_KEY lets a Claude Code session test the app without
    # putting a key in its own ANTHROPIC_API_KEY variable.
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY") or _env("MEDSCRIBE_ANTHROPIC_KEY"))
    app_secret: str = field(default_factory=lambda: _env("APP_SECRET") or secrets.token_urlsafe(32))
    demo_access_code: str = field(default_factory=lambda: _env("DEMO_ACCESS_CODE", "medscribe-demo"))
    openfda_api_key: str = field(default_factory=lambda: _env("OPENFDA_API_KEY"))

    # speech
    assemblyai_region: str = field(default_factory=lambda: _env("ASSEMBLYAI_REGION", "global"))  # global | us | eu
    live_speech_model: str = field(default_factory=lambda: _env("LIVE_SPEECH_MODEL", "universal-3-6-pro"))
    final_speech_model: str = field(default_factory=lambda: _env("FINAL_SPEECH_MODEL", "universal-3-5-pro"))
    # Tests only: point every AssemblyAI call (REST, token, WebSocket) at a local
    # fake server, e.g. http://127.0.0.1:9900. Never set in production.
    assemblyai_test_base: str = field(default_factory=lambda: _env("ASSEMBLYAI_TEST_BASE"))
    # With ASSEMBLYAI_REGION=us or eu, live tokens are minted in that data zone first. If the
    # account cannot use the zone, edge routing (nearest region) is used, unless this is "1".
    assemblyai_strict_zone: bool = field(default_factory=lambda: _env("ASSEMBLYAI_STRICT_ZONE") == "1")
    # This server's public https address. AssemblyAI fetches a recording from a one-time link
    # here only if it refuses the upload. Render sets RENDER_EXTERNAL_URL.
    public_base_url: str = field(default_factory=lambda: (_env("PUBLIC_BASE_URL") or _env("RENDER_EXTERNAL_URL")).rstrip("/"))

    # notes
    note_model: str = field(default_factory=lambda: _env("NOTE_MODEL", "claude-sonnet-5-5"))
    fast_model: str = field(default_factory=lambda: _env("FAST_MODEL", "claude-haiku-5-5"))
    note_effort: str = field(default_factory=lambda: _env("NOTE_EFFORT", "medium"))

    # limits that protect the API bills on a shared demo link
    daily_audio_minutes: int = field(default_factory=lambda: _int("DAILY_AUDIO_MINUTES", 240))
    max_visit_minutes: int = field(default_factory=lambda: _int("MAX_VISIT_MINUTES", 20))
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 60))
    daily_note_runs: int = field(default_factory=lambda: _int("DAILY_NOTE_RUNS", 150))

    # storage
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT / "var"))))

    # external drug data
    rxnav_base: str = field(default_factory=lambda: _env("RXNAV_BASE", "https://rxnav.nlm.nih.gov/REST"))
    openfda_base: str = field(default_factory=lambda: _env("OPENFDA_BASE", "https://api.fda.gov"))

    # the ad zone can run on its own host; the browser calls it here
    adzone_base: str = field(default_factory=lambda: _env("ADZONE_BASE", "/adzone"))

    @property
    def speech_ready(self) -> bool:
        return bool(self.assemblyai_api_key)

    @property
    def notes_ready(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def secure_cookies(self) -> bool:
        return _env("SECURE_COOKIES", "1" if _env("RENDER") else "0") == "1"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
