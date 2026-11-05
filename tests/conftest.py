"""Explicit credentials for synthetic tests; deployment has no default code."""
import os

os.environ.setdefault("DEMO_ACCESS_CODE", "synthetic-test-only")
