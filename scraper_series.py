#!/usr/bin/env python3
"""
Scraper para Scream Queens (lacajalgbt.site) y Riverdale (seriesflixhd.sbs)
Genera el JSON de episodios para agregar al reproductor.
"""
import json
import re
import time
import urllib.request
import urllib.error
from html.parser import HTMLParser

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
}

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}")
        return ""

def extract_iframe_src(html):
    """Extract iframe src from HTML"""
    matches = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for m in matches:
        if any(x in m for x in ['embed', 'player', 'video', 'mega', 'stream', 'waaw', 'dood', 'voe']):
            return m
    if matches:
        return matches[0]
    return None

def extract_data_src(html):
    """Extract data-src from elements"""
    matches = re.findall(r'data-src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for m in matches:
        if 'http' in m:
            return m
    return None

# ─────────────────────────────────────────────────
# SCREAM QUEENS — lacajalgbt.site
# ─────────────────────────────────────────────────
print("=" * 60)
print("SCRAPING SCREAM QUEENS (lacajalgbt.site)")
print("=" * 60)

scream_queens_episodes = []

# Temporada 1: 13 episodios
for ep in range(1, 14):
    url = f"https://lacajalgbt.site/episode/scream-queens-1x{ep}/"
    print(f"  Fetching T1E{ep}: {url}")
    html = fetch(url)
    time.sleep(0.5)
    
    # The player URL is the episode page itself (it loads dynamic content)
    # We use the episode page URL as the player
    episode_data = {
        "novela": "scream_queens",
        "capitulo": ep,
        "temporada": 1,
        "titulo": f"Episodio {ep}",
        "servidores": {
            "lacaja": url
        },
        "ok": True
    }
    scream_queens_episodes.append(episode_data)
    print(f"    ✓ Added episode {ep}")

print(f"\nTotal Scream Queens episodes: {len(scream_queens_episodes)}")

# ─────────────────────────────────────────────────
# RIVERDALE — seriesflixhd.sbs
# ─────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SCRAPING RIVERDALE (seriesflixhd.sbs)")
print("=" * 60)

# Known episode counts per season from the site
RIVERDALE_SEASONS = {
    1: 13,
    2: 22,
    3: 22,
    4: 19,
    5: 19,
    6: 22,
    7: 20,
}

# Titles for Season 1 (from what we scraped)
S1_TITLES = {
    1: "En el margen del río",
    2: "Un toque de maldad",
    3: "Doble de cuerpo",
    4: "La última película",
    5: "Corazón oscuro",
    6: "¡Más rápido, Pussycats!",
    7: "En un lugar solitario",
    8: "Los marginados",
    9: "La gran ilusión",
    10: "El fin de semana perdido",
    11: "Viaje a Riverdale de ida y vuelta",
    12: "Anatomía de un asesinato",
    13: "El dulce porvenir",
}

riverdale_episodes = []

for season, ep_count in RIVERDALE_SEASONS.items():
    print(f"\n  Season {season} ({ep_count} episodes)...")
    
    # Try to get episode titles from season page
    season_url = f"https://seriesflixhd.sbs/temporada/riverdale-znth-{season}/"
    season_html = fetch(season_url)
    time.sleep(0.5)
    
    # Extract episode titles from season page
    ep_titles = {}
    if season == 1:
        ep_titles = S1_TITLES
    else:
        # Try to extract from season page HTML
        # Pattern: links to episodes with titles
        ep_links = re.findall(
            rf'href="https://seriesflixhd\.sbs/episodio/riverdale-znth-{season}x(\d+)/"[^>]*>([^<]+)<',
            season_html
        )
        for num_str, title in ep_links:
            num = int(num_str)
            title = title.strip()
            if title and title != 'Siguiente' and title != 'Anterior':
                ep_titles[num] = title
    
    for ep in range(1, ep_count + 1):
        url = f"https://seriesflixhd.sbs/episodio/riverdale-znth-{season}x{ep}/"
        titulo = ep_titles.get(ep, f"Episodio {ep}")
        
        episode_data = {
            "novela": "riverdale",
            "capitulo": ep,
            "temporada": season,
            "titulo": titulo,
            "servidores": {
                "seriesflix": url
            },
            "ok": True
        }
        riverdale_episodes.append(episode_data)
    
    print(f"    ✓ Added {ep_count} episodes for season {season}")

print(f"\nTotal Riverdale episodes: {len(riverdale_episodes)}")

# ─────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────
output = {
    "scream_queens": scream_queens_episodes,
    "riverdale": riverdale_episodes
}

with open("series_data.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print("\n✅ Data saved to series_data.json")
print(f"   Scream Queens: {len(scream_queens_episodes)} episodes")
print(f"   Riverdale: {len(riverdale_episodes)} episodes")

# Also print the JavaScript-ready format
print("\n" + "=" * 60)
print("JAVASCRIPT DATA PREVIEW")
print("=" * 60)
print(f'scream_queens: {len(scream_queens_episodes)} eps')
print(f'riverdale: {len(riverdale_episodes)} eps')
