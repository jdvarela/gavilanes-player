#!/usr/bin/env python3
"""
Scraper para Pasión de Gavilanes - ennovelas-tv.com
Compatible con Python 3.8+
Genera reproductor HTML con historial de vistos y backup.

Uso:
    python3 scraper_gavilanes.py                        # Todos los caps temp 1
    python3 scraper_gavilanes.py --capitulos 1-20       # Caps 1 al 20
    python3 scraper_gavilanes.py --capitulos 1,5,10     # Caps específicos
    python3 scraper_gavilanes.py --temporada 2          # Temporada 2
    python3 scraper_gavilanes.py --workers 5            # 5 threads
    python3 scraper_gavilanes.py --solo-html            # Solo regenerar HTML
"""

import requests
from bs4 import BeautifulSoup
import base64
import json
import time
import random
import argparse
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from typing import Optional, Dict, List
import shutil
from datetime import datetime

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
BASE_URL = "https://l.ennovelas-tv.com"

TEMPORADAS = {
    1: {"total": 188, "slug_prefix": "pasion-de-gavilanes-capitulo-"},
    2: {"total": 60,  "slug_prefix": "pasion-de-gavilanes-2-capitulo-"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": BASE_URL + "/",
}

OUTPUT_DIR  = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON = os.path.join(OUTPUT_DIR, "capitulos.json")
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "reproductor_gavilanes.html")
BACKUP_DIR  = os.path.join(OUTPUT_DIR, "backups")

DELAY_MIN  = 1.0
DELAY_MAX  = 2.5
MAX_RETRIES = 3
TIMEOUT     = 25


# ──────────────────────────────────────────────
# BACKUP
# ──────────────────────────────────────────────

def make_backup():
    """Crea backup del JSON y HTML si existen"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backed = []
    for src in [OUTPUT_JSON, OUTPUT_HTML]:
        if os.path.exists(src):
            ext  = os.path.splitext(src)[1]
            name = os.path.splitext(os.path.basename(src))[0]
            dst  = os.path.join(BACKUP_DIR, f"{name}_{stamp}{ext}")
            shutil.copy2(src, dst)
            backed.append(dst)
    if backed:
        print(f"💾 Backup guardado en: backups/{stamp}")
    # Rotar: conservar solo últimos 5 backups del JSON
    jsons = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("capitulos_")])
    for old in jsons[:-5]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except Exception:
            pass


# ──────────────────────────────────────────────
# EXTRACCIÓN
# ──────────────────────────────────────────────

def get_episode_url(cap_num, temporada=1):
    prefix = TEMPORADAS[temporada]["slug_prefix"]
    return f"{BASE_URL}/{prefix}{cap_num}/"


def decode_post_token(token):
    # type: (str) -> dict
    try:
        padded  = token + "=" * (4 - len(token) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}


def extract_player_link(soup):
    # type: (BeautifulSoup) -> Optional[str]
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(strip=True).lower()
        if "enn.php" in href or "ver capitulo" in text or "ver_capitulo" in text:
            return href
    return None


def extract_iframes_from_page(url, session):
    # type: (str, requests.Session) -> dict
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return {"error": str(e)}

    soup    = BeautifulSoup(r.text, "html.parser")
    servers = {}

    # Método 1: <li data-server="<iframe ...">
    for li in soup.select("ul.serversList li[data-server]"):
        raw  = li.get("data-server", "")
        name = (li.get_text(strip=True).split() or ["server"])[0].lower()
        inner = BeautifulSoup(raw, "html.parser")
        iframe = inner.find("iframe")
        if iframe and iframe.get("src"):
            servers[name] = iframe["src"]

    # Método 2: iframes directos
    if not servers:
        for iframe in soup.select(".watch iframe, .getEmbed iframe, .serverWatch iframe"):
            src = iframe.get("src", "")
            if src:
                if "vk.com" in src:
                    name = "vk"
                elif "vidspeeds" in src:
                    name = "vidspeeds"
                elif "uqload" in src:
                    name = "uqload"
                else:
                    name = "embed"
                servers[name] = src

    # Método 3: decodificar token de la URL
    if not servers and "post=" in url:
        token = url.split("post=")[-1].split("&")[0]
        data  = decode_post_token(token)
        if data:
            servers = data

    return servers


def scrape_episode(cap_num, temporada, session):
    ep_url = get_episode_url(cap_num, temporada)
    result = {
        "capitulo":   cap_num,
        "temporada":  temporada,
        "url":        ep_url,
        "servidores": {},
        "ok":         False,
        "error":      None,
    }

    r = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(ep_url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 404:
                result["error"] = "404"
                return result
            r.raise_for_status()
            break
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                result["error"] = str(e)
                return result
            time.sleep(2 ** attempt)

    if r is None:
        return result

    soup = BeautifulSoup(r.text, "html.parser")
    player_url = extract_player_link(soup)

    if player_url:
        if "post=" in player_url:
            token   = player_url.split("post=")[-1].split("&")[0]
            decoded = decode_post_token(token)
            if decoded:
                result["servidores"] = decoded
                result["ok"]         = True
                return result

        full = urljoin(BASE_URL, player_url) if not player_url.startswith("http") else player_url
        result["servidores"] = extract_iframes_from_page(full, session)
        result["ok"] = bool(result["servidores"])
    else:
        servers = {}
        for li in soup.select("ul.serversList li[data-server]"):
            raw   = li.get("data-server", "")
            name  = (li.get_text(strip=True).split() or ["server"])[0].lower()
            inner = BeautifulSoup(raw, "html.parser")
            iframe = inner.find("iframe")
            if iframe and iframe.get("src"):
                servers[name] = iframe["src"]
        result["servidores"] = servers
        result["ok"] = bool(servers)

    return result


# ──────────────────────────────────────────────
# MAIN SCRAPER
# ──────────────────────────────────────────────

def run_scraper(cap_list, temporada, workers=3):
    results = []
    total   = len(cap_list)
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"\n📺 Scrapeando {total} capítulos — Temporada {temporada}")
    print(f"   Workers: {workers} | Delay: {DELAY_MIN}-{DELAY_MAX}s\n")

    def scrape_with_delay(cap_num):
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        return scrape_episode(cap_num, temporada, session)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures   = {executor.submit(scrape_with_delay, n): n for n in cap_list}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            data      = future.result()
            results.append(data)
            cap     = data["capitulo"]
            status  = "✅" if data["ok"] else "❌"
            servers = list(data["servidores"].keys()) if data["servidores"] else []
            print(f"  {status} Cap {cap:3d} [{completed:3d}/{total}] → {servers or data.get('error', 'sin datos')}")

    results.sort(key=lambda x: x["capitulo"])
    return results


def parse_capitulos_arg(arg, max_cap):
    # type: (str, int) -> List[int]
    caps = set()
    for part in arg.split(","):
        part = part.strip()
        if "-" in part:
            s, e = part.split("-", 1)
            caps.update(range(int(s), int(e) + 1))
        else:
            caps.add(int(part))
    return sorted(c for c in caps if 1 <= c <= max_cap)


# ──────────────────────────────────────────────
# HTML TEMPLATE — REPRODUCTOR PREMIUM
# ──────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🦅 Pasión de Gavilanes — Reproductor</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Sans:ital,wght@0,300;0,400;0,500;0,600;1,300&display=swap');

  :root {
    --bg:          #09090f;
    --surface:     #111118;
    --surface2:    #18181f;
    --surface3:    #202030;
    --border:      #28283a;
    --accent:      #c8a355;
    --accent-glow: rgba(200,163,85,0.18);
    --red:         #8b1a1a;
    --red-glow:    rgba(139,26,26,0.25);
    --text:        #eae6da;
    --muted:       #6a6a88;
    --success:     #3ecf8e;
    --player-bg:   #000;
    --sidebar-w:   310px;
    --header-h:    66px;
  }

  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

  html { scroll-behavior: smooth; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* ─── SCROLLBAR ─── */
  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--muted); }

  /* ─── HEADER ─── */
  header {
    height: var(--header-h);
    background: rgba(9,9,15,0.92);
    border-bottom: 1px solid var(--border);
    padding: 0 28px;
    display: flex;
    align-items: center;
    gap: 18px;
    position: sticky;
    top: 0;
    z-index: 200;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
  }

  .logo {
    font-family: 'Playfair Display', serif;
    font-size: 1.35rem;
    font-weight: 900;
    color: var(--accent);
    letter-spacing: -0.02em;
    flex-shrink: 0;
    text-shadow: 0 0 30px var(--accent-glow);
  }
  .logo span { color: var(--red); }
  .logo-eagle { font-size: 1.1rem; margin-right: 4px; }

  .header-spacer { flex: 1; }

  .header-pill {
    display: flex;
    align-items: center;
    gap: 7px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 5px 12px;
    font-size: 0.75rem;
    color: var(--muted);
  }
  .header-pill .dot {
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--success);
    box-shadow: 0 0 6px var(--success);
    animation: pulse 2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%       { opacity: 0.5; transform: scale(0.85); }
  }

  .btn-backup {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 7px 14px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text);
    font-family: inherit;
    font-size: 0.78rem;
    cursor: pointer;
    transition: all 0.18s;
  }
  .btn-backup:hover { border-color: var(--accent); color: var(--accent); }
  .btn-backup svg { width: 13px; height: 13px; }

  /* ─── TOAST ─── */
  .toast {
    position: fixed;
    bottom: 28px;
    right: 28px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 18px;
    font-size: 0.82rem;
    color: var(--text);
    z-index: 9999;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    gap: 10px;
    transform: translateY(80px);
    opacity: 0;
    transition: all 0.3s cubic-bezier(0.34,1.56,0.64,1);
    pointer-events: none;
  }
  .toast.show { transform: translateY(0); opacity: 1; }
  .toast.success { border-color: var(--success); }
  .toast.warn { border-color: var(--accent); }

  /* ─── LAYOUT ─── */
  .app {
    display: grid;
    grid-template-columns: var(--sidebar-w) 1fr;
    min-height: calc(100vh - var(--header-h));
  }

  /* ─── SIDEBAR ─── */
  aside {
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    position: sticky;
    top: var(--header-h);
    height: calc(100vh - var(--header-h));
    overflow: hidden;
  }

  .sidebar-head {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .sidebar-title {
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--muted);
    font-weight: 500;
  }

  .search-wrap {
    position: relative;
  }
  .search-wrap svg {
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    width: 14px; height: 14px;
    color: var(--muted);
    pointer-events: none;
  }
  .search-wrap input {
    width: 100%;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 10px 8px 32px;
    color: var(--text);
    font-family: inherit;
    font-size: 0.82rem;
    outline: none;
    transition: border-color 0.2s;
  }
  .search-wrap input:focus { border-color: var(--accent); }
  .search-wrap input::placeholder { color: var(--muted); }

  .filter-tabs {
    display: flex;
    gap: 5px;
  }
  .filter-tab {
    flex: 1;
    padding: 5px 4px;
    text-align: center;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.7rem;
    color: var(--muted);
    font-family: inherit;
    transition: all 0.15s;
    white-space: nowrap;
  }
  .filter-tab.active {
    background: var(--accent);
    color: #000;
    border-color: var(--accent);
    font-weight: 600;
  }

  .season-tabs {
    display: flex;
    gap: 5px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--border);
  }
  .season-tab {
    flex: 1;
    padding: 6px;
    text-align: center;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.74rem;
    color: var(--muted);
    font-family: inherit;
    transition: all 0.15s;
  }
  .season-tab.active {
    background: var(--accent);
    color: #000;
    border-color: var(--accent);
    font-weight: 600;
  }

  .ep-list {
    overflow-y: auto;
    flex: 1;
    padding: 6px;
  }

  .ep-item {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 8px 10px;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s;
    border: 1px solid transparent;
    margin-bottom: 2px;
    position: relative;
  }
  .ep-item:hover { background: var(--surface2); border-color: var(--border); }
  .ep-item.active {
    background: linear-gradient(135deg, rgba(200,163,85,0.12), rgba(139,26,26,0.08));
    border-color: rgba(200,163,85,0.5);
  }
  .ep-item.no-data { opacity: 0.3; cursor: default; }
  .ep-item.no-data:hover { background: transparent; border-color: transparent; }
  .ep-item.watched:not(.active)::after {
    content: '✓';
    position: absolute;
    right: 10px;
    top: 50%;
    transform: translateY(-50%);
    font-size: 0.7rem;
    color: var(--success);
    font-weight: 700;
  }

  .ep-num {
    font-family: 'Playfair Display', serif;
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--accent);
    min-width: 34px;
    text-align: center;
    opacity: 0.75;
  }
  .ep-item.active .ep-num { opacity: 1; }
  .ep-item.watched .ep-num { color: var(--success); }

  .ep-meta { flex: 1; min-width: 0; }
  .ep-title {
    font-size: 0.79rem;
    font-weight: 500;
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .ep-sub {
    font-size: 0.67rem;
    color: var(--muted);
    margin-top: 2px;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .ep-sub .watched-badge {
    color: var(--success);
    font-weight: 600;
  }

  .ep-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--border);
    flex-shrink: 0;
  }
  .ep-item.active .ep-dot { background: var(--accent); }
  .ep-item.watched .ep-dot { background: var(--success); }

  /* ─── MAIN ─── */
  main { display: flex; flex-direction: column; overflow: hidden; }

  .player-wrap {
    background: var(--player-bg);
    position: relative;
    padding-top: 56.25%;
    flex-shrink: 0;
  }
  .player-wrap iframe {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border: none;
  }
  .player-placeholder {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    background: radial-gradient(ellipse at center, #100a08 0%, #09090f 70%);
  }
  .placeholder-eagle {
    font-size: 5rem;
    opacity: 0.15;
    filter: drop-shadow(0 0 30px var(--accent));
    animation: floatEagle 4s ease-in-out infinite;
  }
  @keyframes floatEagle {
    0%, 100% { transform: translateY(0); }
    50%      { transform: translateY(-10px); }
  }
  .placeholder-text { font-size: 0.9rem; color: var(--muted); }

  /* ─── PLAYER INFO BAR ─── */
  .player-info {
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    background: var(--surface);
  }
  .current-ep-label {
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
  }
  .current-ep-title {
    font-family: 'Playfair Display', serif;
    font-size: 1.2rem;
    font-weight: 700;
    line-height: 1.2;
  }
  .current-ep-info { flex: 1; }

  .progress-bar-wrap {
    width: 100%;
    height: 3px;
    background: var(--border);
    border-radius: 2px;
    margin-top: 8px;
    overflow: hidden;
  }
  .progress-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--red));
    border-radius: 2px;
    transition: width 0.4s ease;
  }

  .nav-btns { display: flex; gap: 7px; }
  .nav-btn {
    padding: 8px 16px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text);
    cursor: pointer;
    font-size: 0.8rem;
    font-family: inherit;
    transition: all 0.15s;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .nav-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .nav-btn:disabled { opacity: 0.25; cursor: default; }

  .btn-mark {
    padding: 8px 14px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--muted);
    cursor: pointer;
    font-size: 0.78rem;
    font-family: inherit;
    transition: all 0.15s;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .btn-mark:hover { border-color: var(--success); color: var(--success); }
  .btn-mark.marked { border-color: var(--success); color: var(--success); background: rgba(62,207,142,0.08); }

  /* ─── SERVER BAR ─── */
  .server-bar {
    padding: 12px 24px;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }
  .server-label {
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--muted);
    font-weight: 500;
  }
  .server-btn {
    padding: 5px 13px;
    border-radius: 20px;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text);
    cursor: pointer;
    font-size: 0.75rem;
    font-family: inherit;
    transition: all 0.15s;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .server-btn:hover { border-color: var(--accent); }
  .server-btn.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #000;
    font-weight: 600;
  }
  .no-servers { font-size: 0.8rem; color: var(--red); }

  /* ─── PANEL DE HISTORIAL ─── */
  .panel-tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }
  .panel-tab {
    flex: 1;
    padding: 12px;
    text-align: center;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    cursor: pointer;
    font-family: inherit;
    border: none;
    background: none;
    transition: all 0.15s;
    border-bottom: 2px solid transparent;
  }
  .panel-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .panel-tab:hover:not(.active) { color: var(--text); }

  .panel-body { padding: 20px 24px; flex: 1; overflow-y: auto; }
  .panel-section { display: none; }
  .panel-section.active { display: block; }

  /* Sobre la serie */
  .about-title { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.12em; color: var(--muted); margin-bottom: 10px; }
  .about-desc { font-size: 0.86rem; line-height: 1.75; color: rgba(234,230,218,0.72); max-width: 700px; }
  .badges { display: flex; gap: 7px; flex-wrap: wrap; margin-top: 14px; }
  .badge {
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.7rem;
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--muted);
  }
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 12px;
    margin-top: 20px;
  }
  .stat-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 12px;
    text-align: center;
  }
  .stat-num {
    font-family: 'Playfair Display', serif;
    font-size: 1.5rem;
    color: var(--accent);
    display: block;
  }
  .stat-label { font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }

  /* Historial */
  .history-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
  }
  .history-count { font-size: 0.78rem; color: var(--muted); }
  .btn-clear-history {
    padding: 5px 12px;
    border-radius: 7px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    font-size: 0.73rem;
    font-family: inherit;
    transition: all 0.15s;
  }
  .btn-clear-history:hover { border-color: var(--red); color: var(--red); }

  .history-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
    gap: 8px;
  }
  .history-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 12px 10px;
    cursor: pointer;
    transition: all 0.15s;
    text-align: center;
    position: relative;
  }
  .history-card:hover { border-color: var(--accent); transform: translateY(-2px); }
  .history-card .h-num {
    font-family: 'Playfair Display', serif;
    font-size: 1.4rem;
    color: var(--accent);
    display: block;
  }
  .history-card .h-label { font-size: 0.7rem; color: var(--muted); }
  .history-card .h-date { font-size: 0.62rem; color: var(--border); margin-top: 4px; }
  .history-card .h-remove {
    position: absolute;
    top: 5px; right: 7px;
    font-size: 0.65rem;
    color: var(--border);
    cursor: pointer;
    line-height: 1;
    padding: 2px;
    transition: color 0.15s;
  }
  .history-card .h-remove:hover { color: var(--red); }
  .empty-state {
    text-align: center;
    padding: 40px 20px;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .empty-state .empty-icon { font-size: 2.5rem; display: block; margin-bottom: 10px; opacity: 0.3; }

  /* Backup */
  .backup-zone {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 20px;
  }
  .backup-btn-big {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 22px 16px;
    border-radius: 12px;
    border: 1px solid var(--border);
    background: var(--surface2);
    cursor: pointer;
    transition: all 0.2s;
    font-family: inherit;
    color: var(--text);
  }
  .backup-btn-big:hover { border-color: var(--accent); background: var(--surface3); transform: translateY(-2px); }
  .backup-btn-big svg { width: 26px; height: 26px; color: var(--accent); }
  .backup-btn-big .bb-title { font-size: 0.85rem; font-weight: 600; }
  .backup-btn-big .bb-sub { font-size: 0.7rem; color: var(--muted); }
  .backup-log {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    max-height: 200px;
    overflow-y: auto;
    font-size: 0.75rem;
    color: var(--muted);
    line-height: 1.7;
    font-family: monospace;
  }
  .backup-log-title { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.12em; color: var(--muted); margin-bottom: 8px; font-family: 'DM Sans', sans-serif; }
  .log-line { color: var(--text); }
  .log-line.ok   { color: var(--success); }
  .log-line.warn { color: var(--accent); }

  /* ─── RESPONSIVE ─── */
  @media (max-width: 820px) {
    :root { --sidebar-w: 100%; }
    .app { grid-template-columns: 1fr; }
    aside {
      position: relative;
      top: 0;
      height: 38vh;
      border-right: none;
      border-bottom: 1px solid var(--border);
    }
    .backup-zone { grid-template-columns: 1fr; }
    header { padding: 0 16px; gap: 10px; }
    .btn-backup .btn-label { display: none; }
  }
</style>
</head>
<body>

<!-- ─── TOAST ─── -->
<div class="toast" id="toast"></div>

<!-- ─── HEADER ─── -->
<header>
  <div class="logo">
    <span class="logo-eagle">🦅</span>Pasión de <span>Gavilanes</span>
  </div>
  <div class="header-spacer"></div>
  <div class="header-pill">
    <div class="dot"></div>
    <span id="headerStat">Cargando...</span>
  </div>
  <button class="btn-backup" onclick="quickBackup()">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
      <polyline points="7,10 12,15 17,10"/>
      <line x1="12" y1="15" x2="12" y2="3"/>
    </svg>
    <span class="btn-label">Backup</span>
  </button>
</header>

<!-- ─── APP ─── -->
<div class="app">

  <!-- ─── SIDEBAR ─── -->
  <aside>
    <div class="sidebar-head">
      <div class="sidebar-title">📋 Lista de Capítulos</div>
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input type="number" id="searchCap" placeholder="Buscar capítulo..." min="1">
      </div>
      <div class="filter-tabs">
        <button class="filter-tab active" data-filter="all" onclick="setFilter('all')">Todos</button>
        <button class="filter-tab"        data-filter="ok"  onclick="setFilter('ok')">Con video</button>
        <button class="filter-tab"        data-filter="seen" onclick="setFilter('seen')">Vistos</button>
        <button class="filter-tab"        data-filter="unseen" onclick="setFilter('unseen')">Pendientes</button>
      </div>
    </div>
    <div class="season-tabs" id="seasonTabs"></div>
    <div class="ep-list" id="epList"></div>
  </aside>

  <!-- ─── MAIN ─── -->
  <main>
    <!-- Player -->
    <div class="player-wrap" id="playerWrap">
      <div class="player-placeholder" id="playerPlaceholder">
        <div class="placeholder-eagle">🦅</div>
        <div class="placeholder-text">Selecciona un capítulo para comenzar</div>
      </div>
    </div>

    <!-- Info -->
    <div class="player-info">
      <div class="current-ep-info">
        <div class="current-ep-label">Reproduciendo ahora</div>
        <div class="current-ep-title" id="currentTitle">—</div>
        <div class="progress-bar-wrap" id="progressWrap" style="display:none">
          <div class="progress-bar-fill" id="progressFill"></div>
        </div>
      </div>
      <button class="btn-mark" id="btnMark" onclick="toggleWatched()" style="display:none">
        ✓ Marcar como visto
      </button>
      <div class="nav-btns">
        <button class="nav-btn" id="prevBtn" onclick="navigate(-1)" disabled>← Anterior</button>
        <button class="nav-btn" id="nextBtn" onclick="navigate(1)"  disabled>Siguiente →</button>
      </div>
    </div>

    <!-- Servidores -->
    <div class="server-bar" id="serverBar">
      <span class="server-label">Servidor:</span>
      <span class="no-servers" id="noServersMsg" style="display:none">Sin servidores disponibles</span>
    </div>

    <!-- Tabs de panel inferior -->
    <div class="panel-tabs">
      <button class="panel-tab active" data-panel="about"   onclick="switchPanel('about')">ℹ️ Sobre la serie</button>
      <button class="panel-tab"        data-panel="history" onclick="switchPanel('history')">🕐 Historial</button>
      <button class="panel-tab"        data-panel="backup"  onclick="switchPanel('backup')">💾 Backup</button>
    </div>

    <div class="panel-body">

      <!-- Panel: Sobre la serie -->
      <div class="panel-section active" id="panel-about">
        <div class="about-title">Sinopsis</div>
        <div class="about-desc">
          La historia gira alrededor de los hermanos Reyes: Juan, Óscar y Franco, tres llaneros que llegan a trabajar como peones en la hacienda La Bonita. Allí se enamoran de las tres hijas de Doña Gabriela de Elizondo: Norma, Sara y Jimena. Una historia de amor apasionada, traición, venganza y redención que capturó a millones de televidentes en toda Latinoamérica.
        </div>
        <div class="badges">
          <span class="badge">🇨🇴 Colombia</span>
          <span class="badge">❤️ Drama · Romance</span>
          <span class="badge">📅 2003–2004</span>
          <span class="badge">⭐ 7.8 / 10</span>
          <span class="badge">🌐 Español</span>
          <span class="badge">📺 Televisa / RCN</span>
        </div>
        <div class="stats-grid" id="statsGrid"></div>
      </div>

      <!-- Panel: Historial -->
      <div class="panel-section" id="panel-history">
        <div class="history-toolbar">
          <span class="history-count" id="historyCount">0 vistos</span>
          <button class="btn-clear-history" onclick="clearHistory()">🗑 Limpiar historial</button>
        </div>
        <div class="history-grid" id="historyGrid"></div>
      </div>

      <!-- Panel: Backup -->
      <div class="panel-section" id="panel-backup">
        <div class="backup-zone">
          <button class="backup-btn-big" onclick="exportBackup()">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7,10 12,15 17,10"/><line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            <span class="bb-title">Exportar backup</span>
            <span class="bb-sub">Descarga tu historial en JSON</span>
          </button>
          <button class="backup-btn-big" onclick="document.getElementById('importFile').click()">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7,10 12,15 17,10" transform="rotate(180,12,12)"/>
              <line x1="12" y1="3" x2="12" y2="15" transform="rotate(180,12,9)"/>
            </svg>
            <span class="bb-title">Importar backup</span>
            <span class="bb-sub">Restaura tu historial guardado</span>
          </button>
          <input type="file" id="importFile" accept=".json" style="display:none" onchange="importBackup(event)">
        </div>
        <div class="backup-log-title">📋 Registro de actividad</div>
        <div class="backup-log" id="backupLog">
          <span class="log-line warn">» Sistema listo. Usa los botones de arriba para gestionar tu backup.</span>
        </div>
      </div>

    </div>
  </main>
</div>

<script>
// ═══════════════════════════════════════════════
// DATA (embebida por el scraper)
// ═══════════════════════════════════════════════
const DATA = EPISODES_JSON_PLACEHOLDER;

// ═══════════════════════════════════════════════
// ESTADO GLOBAL
// ═══════════════════════════════════════════════
let currentEp      = null;
let activeSeason   = null;
let activeFilter   = 'all';
let activePanel    = 'about';
const seasons      = [...new Set(DATA.map(e => e.temporada))].sort();

// ─ Historial y preferencias (localStorage) ─
const STORAGE_KEY  = 'gavilanes_history_v2';
const PREFS_KEY    = 'gavilanes_prefs_v2';

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch(e) { return {}; }
}
function saveHistory(h) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(h));
}
function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'); }
  catch(e) { return {}; }
}
function savePrefs(p) {
  localStorage.setItem(PREFS_KEY, JSON.stringify(p));
}

// ═══════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════
function init() {
  buildSeasonTabs();
  const prefs = loadPrefs();
  activeSeason = prefs.lastSeason || seasons[0];
  if (!seasons.includes(activeSeason)) activeSeason = seasons[0];
  updateSeasonTabs();
  renderList();
  updateStats();
  renderHistory();
  logBackup('» Reproductor iniciado.', 'warn');
}

// ═══════════════════════════════════════════════
// SEASON TABS
// ═══════════════════════════════════════════════
function buildSeasonTabs() {
  const el = document.getElementById('seasonTabs');
  el.innerHTML = seasons.map(s =>
    `<button class="season-tab" onclick="switchSeason(${s})" data-season="${s}">Temporada ${s}</button>`
  ).join('');
  if (seasons.length <= 1) el.style.display = 'none';
}
function switchSeason(s) {
  activeSeason = s;
  updateSeasonTabs();
  renderList();
  savePrefs(Object.assign(loadPrefs(), { lastSeason: s }));
}
function updateSeasonTabs() {
  document.querySelectorAll('.season-tab').forEach(t => {
    t.classList.toggle('active', parseInt(t.dataset.season) === activeSeason);
  });
}

// ═══════════════════════════════════════════════
// FILTROS
// ═══════════════════════════════════════════════
function setFilter(f) {
  activeFilter = f;
  document.querySelectorAll('.filter-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.filter === f);
  });
  renderList();
}

function applyFilter(episodes) {
  const history = loadHistory();
  switch(activeFilter) {
    case 'ok':     return episodes.filter(e => hasVideo(e));
    case 'seen':   return episodes.filter(e => history[epKey(e)]);
    case 'unseen': return episodes.filter(e => hasVideo(e) && !history[epKey(e)]);
    default:       return episodes;
  }
}

function hasVideo(ep) {
  return ep.servidores && Object.keys(ep.servidores).length > 0;
}
function epKey(ep) {
  return `t${ep.temporada}c${ep.capitulo}`;
}

// ═══════════════════════════════════════════════
// RENDER LIST
// ═══════════════════════════════════════════════
function renderList(search) {
  if (search === undefined) search = document.getElementById('searchCap').value || '';
  const history  = loadHistory();
  const episodes = DATA.filter(e => e.temporada === activeSeason);
  let filtered   = applyFilter(episodes);
  if (search) filtered = filtered.filter(e => e.capitulo.toString().includes(search));

  const list = document.getElementById('epList');
  if (filtered.length === 0) {
    list.innerHTML = '<div class="empty-state"><span class="empty-icon">📭</span>Sin resultados</div>';
    return;
  }

  list.innerHTML = filtered.map(ep => {
    const ok       = hasVideo(ep);
    const isActive = currentEp && currentEp.capitulo === ep.capitulo && currentEp.temporada === ep.temporada;
    const watched  = !!history[epKey(ep)];
    const servers  = ok ? Object.keys(ep.servidores).join(' · ') : 'sin video';
    const watchedBadge = watched ? '<span class="watched-badge">· visto</span>' : '';
    return `<div class="ep-item ${!ok ? 'no-data' : ''} ${isActive ? 'active' : ''} ${watched && !isActive ? 'watched' : ''}"
                 onclick="${ok ? `loadEp(${ep.capitulo},${ep.temporada})` : ''}"
                 data-cap="${ep.capitulo}" data-season="${ep.temporada}">
      <div class="ep-num">${ep.capitulo}</div>
      <div class="ep-meta">
        <div class="ep-title">Capítulo ${ep.capitulo}</div>
        <div class="ep-sub">${servers}${watchedBadge}</div>
      </div>
      <div class="ep-dot"></div>
    </div>`;
  }).join('');
}

// ═══════════════════════════════════════════════
// LOAD EPISODE
// ═══════════════════════════════════════════════
function loadEp(cap, season) {
  const ep = DATA.find(e => e.capitulo === cap && e.temporada === season);
  if (!ep || !hasVideo(ep)) return;

  currentEp = ep;

  // auto-marcar si ya estaba en historial o mantener estado
  renderList();
  const active = document.querySelector('.ep-item.active');
  if (active) active.scrollIntoView({ block: 'nearest', behavior: 'smooth' });

  document.getElementById('currentTitle').textContent = `Temporada ${ep.temporada} — Capítulo ${ep.capitulo}`;

  // Barra de progreso
  const valid  = DATA.filter(e => e.temporada === activeSeason && hasVideo(e));
  const idx    = valid.findIndex(e => e.capitulo === cap);
  const pct    = valid.length > 1 ? Math.round((idx / (valid.length - 1)) * 100) : 0;
  document.getElementById('progressWrap').style.display = 'block';
  document.getElementById('progressFill').style.width   = pct + '%';

  // Botón "marcar como visto"
  const btnMark = document.getElementById('btnMark');
  btnMark.style.display = '';
  updateMarkBtn(ep);

  // Servidores
  renderServerBtns(ep);

  // Nav
  document.getElementById('prevBtn').disabled = idx <= 0;
  document.getElementById('nextBtn').disabled = idx >= valid.length - 1;

  // Guardar preferencia
  savePrefs(Object.assign(loadPrefs(), { lastSeason: season, lastCap: cap }));
}

function renderServerBtns(ep) {
  const bar  = document.getElementById('serverBar');
  const keys = Object.keys(ep.servidores);
  bar.querySelectorAll('.server-btn').forEach(b => b.remove());
  document.getElementById('noServersMsg').style.display = keys.length ? 'none' : '';

  keys.forEach((key, i) => {
    const btn = document.createElement('button');
    btn.className = 'server-btn' + (i === 0 ? ' active' : '');
    btn.innerHTML = serverIcon(key) + ' ' + key.toUpperCase();
    btn.onclick = () => {
      bar.querySelectorAll('.server-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      setIframe(ep.servidores[key]);
    };
    bar.appendChild(btn);
  });

  if (keys.length > 0) setIframe(ep.servidores[keys[0]]);
}

function serverIcon(key) {
  if (key === 'vk')        return '▶';
  if (key === 'vidspeeds') return '⚡';
  if (key === 'uqload')    return '☁';
  return '▶';
}

function setIframe(src) {
  const wrap   = document.getElementById('playerWrap');
  const ph     = document.getElementById('playerPlaceholder');
  if (ph) ph.style.display = 'none';
  wrap.querySelectorAll('iframe').forEach(f => f.remove());
  const iframe       = document.createElement('iframe');
  iframe.src         = src;
  iframe.scrolling   = 'no';
  iframe.frameBorder = '0';
  iframe.setAttribute('allowfullscreen', '');
  iframe.setAttribute('webkitallowfullscreen', '');
  iframe.setAttribute('mozallowfullscreen', '');
  iframe.setAttribute('allow', 'autoplay; fullscreen');
  wrap.appendChild(iframe);
}

function navigate(dir) {
  if (!currentEp) return;
  const valid = DATA.filter(e => e.temporada === activeSeason && hasVideo(e));
  const idx   = valid.findIndex(e => e.capitulo === currentEp.capitulo);
  const next  = valid[idx + dir];
  if (next) loadEp(next.capitulo, next.temporada);
}

// ═══════════════════════════════════════════════
// MARCAR COMO VISTO
// ═══════════════════════════════════════════════
function toggleWatched() {
  if (!currentEp) return;
  const history = loadHistory();
  const key     = epKey(currentEp);
  if (history[key]) {
    delete history[key];
    showToast('Capítulo desmarcado como visto', '👁');
  } else {
    history[key] = { watchedAt: new Date().toISOString(), cap: currentEp.capitulo, season: currentEp.temporada };
    showToast(`Capítulo ${currentEp.capitulo} marcado como visto ✓`, '✅');
  }
  saveHistory(history);
  updateMarkBtn(currentEp);
  renderList();
  renderHistory();
  updateStats();
}

function updateMarkBtn(ep) {
  const history = loadHistory();
  const btn     = document.getElementById('btnMark');
  if (history[epKey(ep)]) {
    btn.classList.add('marked');
    btn.textContent = '✓ Visto';
  } else {
    btn.classList.remove('marked');
    btn.textContent = '✓ Marcar como visto';
  }
}

// ═══════════════════════════════════════════════
// PANEL TABS
// ═══════════════════════════════════════════════
function switchPanel(p) {
  activePanel = p;
  document.querySelectorAll('.panel-tab').forEach(t => t.classList.toggle('active', t.dataset.panel === p));
  document.querySelectorAll('.panel-section').forEach(s => s.classList.toggle('active', s.id === 'panel-' + p));
  if (p === 'history') renderHistory();
}

// ═══════════════════════════════════════════════
// HISTORIAL
// ═══════════════════════════════════════════════
function renderHistory() {
  const history = loadHistory();
  const items   = Object.values(history).sort((a,b) => new Date(b.watchedAt) - new Date(a.watchedAt));
  const grid    = document.getElementById('historyGrid');
  document.getElementById('historyCount').textContent = `${items.length} capítulo${items.length !== 1 ? 's' : ''} visto${items.length !== 1 ? 's' : ''}`;

  if (!items.length) {
    grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><span class="empty-icon">📺</span>Aún no has marcado capítulos como vistos.<br>Usa el botón "Marcar como visto" al reproducir.</div>';
    return;
  }

  grid.innerHTML = items.map(item => {
    const d   = new Date(item.watchedAt);
    const fmt = d.toLocaleDateString('es-ES', { day:'2-digit', month:'short' });
    const key = `t${item.season}c${item.cap}`;
    return `<div class="history-card" onclick="loadEp(${item.cap},${item.season})" title="Ir al capítulo">
      <span class="h-remove" onclick="event.stopPropagation();removeFromHistory('${key}')">✕</span>
      <span class="h-num">${item.cap}</span>
      <span class="h-label">T${item.season} · Cap ${item.cap}</span>
      <div class="h-date">${fmt}</div>
    </div>`;
  }).join('');
}

function removeFromHistory(key) {
  const h = loadHistory();
  delete h[key];
  saveHistory(h);
  renderHistory();
  updateStats();
  renderList();
  if (currentEp && key === epKey(currentEp)) updateMarkBtn(currentEp);
  showToast('Eliminado del historial', '🗑');
}

function clearHistory() {
  if (!confirm('¿Limpiar todo el historial de vistos?')) return;
  saveHistory({});
  renderHistory();
  updateStats();
  renderList();
  if (currentEp) updateMarkBtn(currentEp);
  showToast('Historial limpiado', '🗑');
  logBackup('» Historial limpiado por el usuario.', 'warn');
}

// ═══════════════════════════════════════════════
// BACKUP
// ═══════════════════════════════════════════════
function exportBackup() {
  const payload = {
    version:    2,
    exportedAt: new Date().toISOString(),
    history:    loadHistory(),
    prefs:      loadPrefs(),
  };
  const blob   = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url    = URL.createObjectURL(blob);
  const a      = document.createElement('a');
  a.href       = url;
  a.download   = `gavilanes_backup_${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('Backup exportado correctamente', '💾');
  logBackup(`✓ Backup exportado — ${Object.keys(loadHistory()).length} registros`, 'ok');
}

function importBackup(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    try {
      const data = JSON.parse(e.target.result);
      if (!data.history) throw new Error('Formato inválido');
      // Merge en lugar de reemplazar
      const existing = loadHistory();
      const merged   = Object.assign({}, existing, data.history);
      saveHistory(merged);
      if (data.prefs) savePrefs(Object.assign(loadPrefs(), data.prefs));
      renderHistory();
      updateStats();
      renderList();
      if (currentEp) updateMarkBtn(currentEp);
      showToast(`Backup importado: ${Object.keys(data.history).length} registros`, '✅');
      logBackup(`✓ Backup importado — ${Object.keys(data.history).length} registros fusionados`, 'ok');
    } catch(err) {
      showToast('Error al importar: ' + err.message, '❌');
      logBackup('✗ Error al importar: ' + err.message);
    }
  };
  reader.readAsText(file);
  event.target.value = '';
}

function quickBackup() {
  exportBackup();
}

function logBackup(msg, cls) {
  const log  = document.getElementById('backupLog');
  const line = document.createElement('div');
  line.className = 'log-line' + (cls ? ' ' + cls : '');
  const t    = new Date().toLocaleTimeString('es-ES', { hour:'2-digit', minute:'2-digit', second:'2-digit' });
  line.textContent = `[${t}] ${msg}`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

// ═══════════════════════════════════════════════
// STATS
// ═══════════════════════════════════════════════
function updateStats() {
  const history  = loadHistory();
  const total    = DATA.length;
  const withVid  = DATA.filter(hasVideo).length;
  const watched  = Object.keys(history).length;

  document.getElementById('headerStat').textContent = `${withVid}/${total} con video · ${watched} vistos`;

  document.getElementById('statsGrid').innerHTML = `
    <div class="stat-card"><span class="stat-num">${total}</span><span class="stat-label">Capítulos totales</span></div>
    <div class="stat-card"><span class="stat-num">${withVid}</span><span class="stat-label">Con video</span></div>
    <div class="stat-card"><span class="stat-num">${watched}</span><span class="stat-label">Vistos</span></div>
    <div class="stat-card"><span class="stat-num">${seasons.length}</span><span class="stat-label">Temporadas</span></div>
    <div class="stat-card"><span class="stat-num">${withVid > 0 ? Math.round((watched/withVid)*100) : 0}%</span><span class="stat-label">Progreso</span></div>
  `;
}

// ═══════════════════════════════════════════════
// TOAST
// ═══════════════════════════════════════════════
let toastTimer = null;
function showToast(msg, icon) {
  const el  = document.getElementById('toast');
  el.innerHTML = (icon ? icon + ' ' : '') + msg;
  el.className = 'toast show' + (icon === '✅' || icon === '💾' ? ' success' : icon === '❌' ? '' : ' warn');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 3000);
}

// ═══════════════════════════════════════════════
// SEARCH
// ═══════════════════════════════════════════════
document.getElementById('searchCap').addEventListener('input', e => {
  renderList(e.target.value || '');
});
document.getElementById('searchCap').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const val = parseInt(e.target.value);
    if (val) {
      const ep = DATA.find(d => d.capitulo === val && d.temporada === activeSeason);
      if (ep && hasVideo(ep)) loadEp(val, activeSeason);
    }
  }
});

// ═══════════════════════════════════════════════
// AUTO-RESUME
// ═══════════════════════════════════════════════
function autoResume() {
  const prefs = loadPrefs();
  if (prefs.lastCap && prefs.lastSeason) {
    const ep = DATA.find(e => e.capitulo === prefs.lastCap && e.temporada === prefs.lastSeason);
    if (ep && hasVideo(ep)) {
      showToast(`¿Continuar desde T${prefs.lastSeason} Cap ${prefs.lastCap}? Haz clic en el capítulo.`, '▶');
      // Scroll al último episodio en la sidebar
      setTimeout(() => {
        const el = document.querySelector(`[data-cap="${prefs.lastCap}"][data-season="${prefs.lastSeason}"]`);
        if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }, 500);
    }
  }
}

// ═══════════════════════════════════════════════
// START
// ═══════════════════════════════════════════════
init();
autoResume();
</script>
</body>
</html>
"""


def generate_html(episodes):
    # type: (list) -> str
    clean = [
        {
            "capitulo":  ep["capitulo"],
            "temporada": ep["temporada"],
            "servidores": ep.get("servidores", {}),
            "ok": ep.get("ok", False),
        }
        for ep in episodes
    ]
    json_data = json.dumps(clean, ensure_ascii=False, separators=(',', ':'))
    return HTML_TEMPLATE.replace("EPISODES_JSON_PLACEHOLDER", json_data)


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scraper Pasión de Gavilanes → HTML")
    parser.add_argument("--capitulos", type=str, default=None)
    parser.add_argument("--temporada", type=int, default=1, choices=[1, 2])
    parser.add_argument("--workers",   type=int, default=3)
    parser.add_argument("--solo-html", action="store_true")
    parser.add_argument("--output",    type=str, default=OUTPUT_HTML)
    parser.add_argument("--no-backup", action="store_true", help="No crear backup previo")
    args = parser.parse_args()

    # ── Modo solo-HTML
    if args.solo_html:
        if not os.path.exists(OUTPUT_JSON):
            print(f"❌ No encontré {OUTPUT_JSON}. Ejecuta el scraper primero.")
            sys.exit(1)
        if not args.no_backup:
            make_backup()
        with open(OUTPUT_JSON, encoding="utf-8") as f:
            episodes = json.load(f)
        html = generate_html(episodes)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ HTML regenerado → {args.output}")
        return

    temporada = args.temporada
    max_cap   = TEMPORADAS[temporada]["total"]

    if args.capitulos:
        cap_list = parse_capitulos_arg(args.capitulos, max_cap)
    else:
        cap_list = list(range(1, max_cap + 1))

    if not cap_list:
        print("❌ Lista de capítulos vacía.")
        sys.exit(1)

    # Backup previo
    if not args.no_backup:
        make_backup()

    # Cargar progreso previo
    existing = {}
    if os.path.exists(OUTPUT_JSON):
        try:
            with open(OUTPUT_JSON, encoding="utf-8") as f:
                prev = json.load(f)
            existing = {(e["capitulo"], e["temporada"]): e for e in prev}
            print(f"📂 Cargando {len(existing)} capítulos previos desde caché")
        except Exception:
            pass

    to_scrape  = [c for c in cap_list if (c, temporada) not in existing or not existing[(c, temporada)].get("ok")]
    already_ok = [c for c in cap_list if (c, temporada) in existing and existing[(c, temporada)].get("ok")]

    if already_ok:
        print(f"⏭️  Saltando {len(already_ok)} capítulos ya en caché")
    if to_scrape:
        new_results = run_scraper(to_scrape, temporada, workers=args.workers)
        for r in new_results:
            existing[(r["capitulo"], r["temporada"])] = r
    else:
        print("✅ Todos los capítulos ya están en caché")

    all_episodes = sorted(existing.values(), key=lambda x: (x["temporada"], x["capitulo"]))

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_episodes, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON guardado → {OUTPUT_JSON}")

    html = generate_html(all_episodes)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    ok_count = sum(1 for e in all_episodes if e.get("ok"))
    print(f"🎬 HTML generado → {args.output}")
    print(f"📊 {ok_count}/{len(all_episodes)} capítulos con video\n")


if __name__ == "__main__":
    main()
