"""
fetch_fighter_photos.py — scrape UFC athlete headshots.

For each fighter in fighter_summary.json, fetches the UFC.com athlete page
and extracts the og:image URL (fighter headshot / full-body render).

Output: data/processed/fighter_photos.json  →  {fighter_id: photo_url}

Run:
    python src/fetch_fighter_photos.py
    python src/fetch_fighter_photos.py --force   # re-fetch already cached entries
"""
import sys
import re
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_json, save_json, DATA_PROC, get_logger, slugify, fetch_html

log = get_logger("photos")

PHOTOS_JSON = DATA_PROC / "fighter_photos.json"
UFC_BASE    = "https://www.ufc.com/athlete"

SLUG_ALIASES = {
    "Juan Adrian Martinetti": ["juan-martinetti"],
}


def _extract_og_image(html: str) -> str | None:
    """Pull the og:image URL from a UFC athlete page."""
    m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\'](https?://[^"\']+)["\']', html)
    if m:
        return m.group(1)
    m = re.search(r'content=["\'](https?://[^"\']+)["\']\s+property=["\']og:image["\']', html)
    if m:
        return m.group(1)
    return None


def _extract_headshot(html: str) -> str | None:
    """Fallback: first athlete-bio or headshot img src."""
    patterns = [
        r'class="[^"]*athlete-bio[^"]*"[^>]*src=["\'](https?://[^"\']+)["\']',
        r'class="[^"]*headshot[^"]*"[^>]*src=["\'](https?://[^"\']+)["\']',
        r'class="[^"]*athlete-thumb[^"]*"[^>]*src=["\'](https?://[^"\']+)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return None


def fetch_photos(force: bool = False) -> dict[str, str]:
    fighters = load_json(DATA_PROC / "fighter_summary.json")
    photos: dict[str, str] = {}
    if PHOTOS_JSON.exists() and not force:
        photos = load_json(PHOTOS_JSON)

    total = len(fighters)
    updated = 0

    for i, f in enumerate(fighters, 1):
        fid  = f.get("fighter_id", "")
        name = f.get("name", "")
        if not fid or not name:
            continue

        if fid in photos and not force:
            log.debug("[%d/%d] %s — cached", i, total, name)
            continue

        slugs = [slugify(name)]
        for alias in SLUG_ALIASES.get(name, []):
            if alias not in slugs:
                slugs.append(alias)

        try:
            photo = None
            checked_urls = []
            for slug in slugs:
                url = f"{UFC_BASE}/{slug}"
                checked_urls.append(url)
                cache_path = DATA_PROC / "photo_cache" / f"{fid}-{slug}.html"
                html = fetch_html(url, cache_path=cache_path if not force else None, throttle=1.0)
                photo = _extract_og_image(html) or _extract_headshot(html)
                if photo:
                    break
            if photo:
                photos[fid] = photo
                updated += 1
                log.info("[%d/%d] %s → %s", i, total, name, photo[:60])
            else:
                log.warning("[%d/%d] %s — no image found at %s", i, total, name, url)
        except Exception as exc:
            log.warning("[%d/%d] %s — fetch failed: %s", i, total, name, exc)

    save_json(photos, PHOTOS_JSON)
    log.info("Done. %d photos saved (%d new) → %s", len(photos), updated, PHOTOS_JSON)
    return photos


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-fetch all, ignoring cache")
    args = parser.parse_args()
    fetch_photos(force=args.force)
