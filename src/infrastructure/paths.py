"""Canonical runtime-data directory locations.

Generated/fetched artifacts — broker instrument dumps, accumulating OI
history, fetched FII/DII CSV data, and similar — used to live scattered
directly inside the package (brokers/_scrip_master_cache.json,
oi/oi_history_log.parquet, analytics/nse_fii_dii_flow_history.csv, each
computed as os.path.dirname(__file__) relative to whichever module writes
it). That meant the Python package wasn't just code, and — the concrete
bug this caused — moving a module between packages (e.g. the oi_analysis.py
-> oi/oi_analysis.py migration) silently changed the file's on-disk
location too, fragmenting what should have been one continuous history
into two files with no fallback between them.

Everything regenerable/accumulative now lives under runtime/, one level
up from any package, addressed through this module instead of each
writer recomputing its own path.

NOTE (2026-07-31): runtime/ used to sit *inside* the backend/ package
(backend/runtime/cache/...), which meant the package directory wasn't
just source -- 40MB+ of accumulated cache/history data lived inside it
too, and any tool that treats "the package directory" as "the source
tree" had to know to skip it. runtime/ is now a sibling of backend/, one
level up, so the package directory contains only source. Set the
RUNTIME_DIR environment variable to override the location entirely
(e.g. in a deployment where you want it under /var/lib/... instead).
"""

import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
RUNTIME_DIR = os.getenv("RUNTIME_DIR", os.path.join(PROJECT_ROOT, "runtime"))
CACHE_DIR = os.path.join(RUNTIME_DIR, "cache")

os.makedirs(CACHE_DIR, exist_ok=True)
