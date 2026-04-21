"""
utils.py — shared helpers: HTTP client, caching, logging, method normalisation.
"""
import logging
import re
import time
import json
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent
DATA_RAW   = BASE_DIR / "data" / "raw"
DATA_PROC  = BASE_DIR / "data" / "processed"
OUTPUT_DIR = BASE_DIR / "output"

for _d in [DATA_RAW / "fighters", DATA_RAW / "fights", DATA_RAW / "cache", DATA_PROC, OUTPUT_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(name)

# ── HTTP ───────────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)
    return session

_session = _make_session()

def fetch_html(url: str, cache_path: Path | None = None, throttle: float = 0.8) -> str:
    """Fetch URL with optional file-based caching and polite throttling."""
    log = get_logger("http")
    if cache_path and cache_path.exists() and cache_path.stat().st_size > 0:
        log.debug("Cache hit: %s", cache_path.name)
        return cache_path.read_text(encoding="utf-8")
    log.info("GET %s", url)
    resp = _session.get(url, timeout=25)
    resp.raise_for_status()
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(resp.text, encoding="utf-8")
    time.sleep(throttle)
    return resp.text

# ── JSON ───────────────────────────────────────────────────────────────────────
def save_json(data, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

# ── Method normalisation ───────────────────────────────────────────────────────
_METHOD_MAP: dict[str, str] = {
    "KO":                    "KO/TKO",
    "TKO":                   "KO/TKO",
    "KO/TKO":                "KO/TKO",
    "SUBMISSION":            "Submission",
    "SUB":                   "Submission",
    "TECHNICAL SUBMISSION":  "Submission",
    "U-DEC":                 "Decision",
    "S-DEC":                 "Decision",
    "M-DEC":                 "Decision",
    "UNANIMOUS DECISION":    "Decision",
    "SPLIT DECISION":        "Decision",
    "MAJORITY DECISION":     "Decision",
    "DECISION":              "Decision",
    "DQ":                    "DQ",
    "DISQUALIFICATION":      "DQ",
    "NC":                    "NC",
    "NO CONTEST":            "NC",
    "COULD NOT CONTINUE":    "Other",
    "OVERTURNED":            "Other",
    "OTHER":                 "Other",
}

def normalise_method(raw: str) -> str:
    """Map raw ufcstats method string to canonical category."""
    if not raw:
        return "Other"
    key = raw.strip().upper().replace(".", "")
    if key in _METHOD_MAP:
        return _METHOD_MAP[key]
    # partial matches
    if "KO" in key or "TKO" in key:
        return "KO/TKO"
    if "SUB" in key:
        return "Submission"
    if "DEC" in key:
        return "Decision"
    if "DQ" in key or "DISQ" in key:
        return "DQ"
    if "NC" in key or "NO CONTEST" in key:
        return "NC"
    return "Other"

def normalise_result(raw: str) -> str:
    """Normalise fight result to W / L / D / NC."""
    if not raw:
        return ""
    key = raw.strip().lower()
    mapping = {"win": "W", "w": "W", "loss": "L", "l": "L",
               "lose": "L", "draw": "D", "d": "D", "nc": "NC",
               "no contest": "NC"}
    return mapping.get(key, raw.strip().upper())

# ── Stat parsing ───────────────────────────────────────────────────────────────
def parse_fraction(text: str) -> tuple[int, int]:
    """Parse '45 of 90' → (45, 90). Returns (0, 0) on failure."""
    m = re.match(r"(\d+)\s+of\s+(\d+)", (text or "").strip(), re.I)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

def safe_int(text, default: int = 0) -> int:
    try:
        return int(re.sub(r"[^\d]", "", str(text)))
    except (TypeError, ValueError):
        return default

def safe_float(text, default=None):
    try:
        cleaned = re.sub(r"[^0-9.]", "", str(text or ""))
        return float(cleaned) if cleaned else default
    except ValueError:
        return default

def pct_to_float(text: str):
    """Convert '51%' → 0.51. Returns None on failure."""
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(text))
    return round(float(m.group(1)) / 100.0, 4) if m else None

def slugify(name: str) -> str:
    """Convert 'Aljamain Sterling' → 'aljamain-sterling'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
