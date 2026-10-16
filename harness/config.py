"""Settings and business rules in one place, so a finance lead can read them."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Minimal .env support (no extra dependency). Real environment variables win.
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            if _v.strip():
                os.environ.setdefault(_k.strip(), _v.strip())
DATA = ROOT / "data"
PO_FILE = DATA / "purchase_orders.json"
PAID_FILE = DATA / "paid_invoices.json"
SAMPLES = DATA / "samples"
RUNTIME = DATA / "runtime"

# --- Model ---------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
ANTHROPIC_URL = os.environ.get("ANTHROPIC_URL", "https://api.anthropic.com/v1/messages")
LLM_TIMEOUT_S = 60

# "llm" | "baseline". Default: llm when a key is present, otherwise baseline.
EXTRACTOR = os.environ.get("INVOICE_EXTRACTOR") or ("llm" if ANTHROPIC_API_KEY else "baseline")

# --- Business rules (AP policy) -----------------------------------------
# Unit price may drift this much above the PO before we flag it.
PRICE_TOLERANCE_PCT = 0.5      # percent
PRICE_TOLERANCE_ABS = 0.01     # dollars per unit
# Rounding slack when re-checking the supplier's arithmetic.
MATH_TOLERANCE = 0.02          # dollars
# Description similarity needed to match a line that has no item code.
DESCRIPTION_MATCH_MIN = 0.60
# Same supplier + same total within this many days = possible duplicate.
DUPLICATE_WINDOW_DAYS = 14
# Supplier name similarity needed to accept a printed name as the PO's supplier.
SUPPLIER_MATCH_MIN = 0.55
