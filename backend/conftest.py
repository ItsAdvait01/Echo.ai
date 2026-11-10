"""Pytest bootstrap for the backend test suite.

Tests must never share the demo workflow database: they point A2's repository
at a temporary SQLite file before any application module imports it. The demo
run uses ``ECHO_MEMORY_DB=echo_demo.db`` instead.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "ECHO_MEMORY_DB", os.path.join(tempfile.gettempdir(), "echo_test_suite.db")
)
