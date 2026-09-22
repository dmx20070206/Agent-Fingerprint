#!/usr/bin/env python3
"""Build a small, reproducible local Wikipedia subset from real Wikipedia pages."""
from pathlib import Path
from urllib.parse import urljoin, urlparse
import hashlib
import html as htmlmod
import re, time, requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent / "wikipedia"
ASSETS = ROOT / "assets"
ROOT.mkdir(parents=True, exist_ok=True); ASSETS.mkdir(exist_ok=True)
PLACEHOLDER = ASSETS / "image-placeholder.svg"
if not PLACEHOLDER.exists():
    PLACEHOLDER.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160" viewBox="0 0 240 160"><rect width="240" height="160" fill="#eaecf0"/><path d="M24 132l48-52 30 30 24-26 90 48z" fill="#a2a9b1"/><circle cx="78" cy="52" r="16" fill="#72777d"/><text x="120" y="150" text-anchor="middle" font-family="sans-serif" font-size="12" fill="#54595d">Image omitted in offline mirror</text></svg>', encoding="utf-8")
PAGES = [
    "Artificial_intelligence", "Machine_learning", "Deep_learning",
    "Natural_language_processing", "Computer_vision", "Robotics",
    "Knowledge_representation_and_reasoning", "Expert_system",
    "Artificial_neural_network", "Reinforcement_learning",
    "Generative_artificial_intelligence", "History_of_artificial_intelligence",
    "Turing_test", "AI_safety", "Ethics_of_artificial_intelligence",
]
BASE = "https://en.wikipedia.org/wiki/"
S = requests.Session(); S.headers.update({"User-Agent":"OfflineWikiBuilder/1.0 (educational local mirror)"})

def safe_name(title): return re.sub(r"[^A-Za-z0-9_.-]+", "_", title) + ".html"

def fetch(url):
    for attempt in range(3):
        try:
            r = S.get(url, timeout=60); r.raise_for_status(); return r
        except Exception:
            if attempt == 2: raise
            time.sleep(1.5)

def store_image(url):
    """Download a Wikimedia image, capped at 2 MiB, and return its local path."""
    url = urljoin("https:", htmlmod.unescape(url))
    host = urlparse(url).hostname or ""
    if not host.endswith("wikimedia.org") and not host.endswith("wikipedia.org"):
        return None
    try:
        r = S.get(url, timeout=45, stream=True); r.raise_for_status()
        if int(r.headers.get("content-length", 0) or 0) > 2 * 1024 * 1024:
            return None
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}:
            suffix = ".img"
        name = hashlib.sha256(url.encode()).hexdigest()[:20] + suffix
        target = ASSETS / name
        if target.exists(): return "assets/" + name
        data = bytearray()
        for chunk in r.iter_content(65536):
            data.extend(chunk)
            if len(data) > 2 * 1024 * 1024: return None
        target.write_bytes(data)
        return "assets/" + name
    except requests.RequestException:
        return None

def main():
    local = {p: safe_name(p) for p in PAGES}
    # Fetch article HTML and make links among the selected entries local.
    for i, title in enumerate(PAGES, 1):
        print(f"[{i}/{len(PAGES)}] {title}")
        soup = BeautifulSoup(fetch(BASE + title).text, "html.parser")
        for a in soup.select("a[href]"):
            href = htmlmod.unescape(a.get("href", ""))
            m = re.match(r"^(?:https?://en\.wikipedia\.org)?/wiki/([^#?]+)", href)
            if m:
                target = m.group(1)
                if target in local: a["href"] = local[target] + ("#" + href.split("#",1)[1] if "#" in href else "")
        # Bundle up to eight useful images per article to keep the mirror modest.
        downloaded = 0
        for img in soup.select("img[src]"):
            if downloaded >= 8:
                img["src"] = "assets/image-placeholder.svg"
                img.attrs.pop("srcset", None)
                continue
            path = store_image(img["src"])
            if path:
                img["src"] = path
                img.attrs.pop("srcset", None)
                downloaded += 1
            else:
                img["src"] = "assets/image-placeholder.svg"
                img.attrs.pop("srcset", None)
        # Video/audio media are intentionally omitted to keep the mirror offline and small.
        for source in soup.select("source[src]"):
            source["src"] = ""
        for media in soup.select("video, audio"):
            media.decompose()
        # Remove scripts that could make the mirror call the live site.
        for tag in soup.find_all("script"): tag.decompose()
        # Point stylesheets at a bundled copy; retain article markup and attribution.
        for link in soup.select('link[rel="stylesheet"]'):
            link["href"] = "assets/style.css"
        (ROOT / local[title]).write_text(str(soup), encoding="utf-8")

    # Download the main Vector stylesheet used by the pages.
    css_url = "https://en.wikipedia.org/w/load.php?lang=en&modules=site.styles|skins.vector.styles&only=styles&skin=vector-2022"
    css = fetch(css_url).text
    (ASSETS / "style.css").write_text(css, encoding="utf-8")

    # A lightweight local home page makes the subset discoverable.
    links = "\n".join(f'<li><a href="{local[p]}">{p.replace("_", " ")}</a></li>' for p in PAGES)
    index = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Local Wikipedia — Artificial intelligence</title><link rel="stylesheet" href="assets/style.css"><style>body{{max-width:1000px;margin:2rem auto;padding:0 1rem;font-family:system-ui,sans-serif}}.hero{{background:#f8f9fa;border:1px solid #a2a9b1;padding:1.5rem;margin-bottom:1.5rem}}li{{margin:.45rem 0}}small{{color:#54595d}}</style></head><body><div class="hero"><h1>Local Wikipedia</h1><p>A curated offline subset of the English Wikipedia focused on artificial intelligence.</p><small>15 articles captured from live Wikipedia on build date; article text remains under its original CC BY-SA/GFDL terms.</small></div><h2>Explore the collection</h2><ul>{links}</ul><p><a href="https://en.wikipedia.org/wiki/Artificial_intelligence">View the live article on Wikipedia ↗</a></p></body></html>'''
    (ROOT / "index.html").write_text(index, encoding="utf-8")
    print(f"Built {len(PAGES)} articles in {ROOT}")

if __name__ == "__main__": main()
