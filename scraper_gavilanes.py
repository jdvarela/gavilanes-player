#!/usr/bin/env python3
"""
Scraper Multi-Novela — ennovelas-tv.com
Compatible con Python 3.8+

Uso:
    python3 scraper_gavilanes.py                            # Scrapea todas las novelas
    python3 scraper_gavilanes.py --novela gavilanes         # Solo Gavilanes
    python3 scraper_gavilanes.py --novela bella             # Solo Bella Calamidades
    python3 scraper_gavilanes.py --capitulos 1-20           # Rango de caps
    python3 scraper_gavilanes.py --temporada 2              # Temporada 2 (solo Gavilanes)
    python3 scraper_gavilanes.py --workers 4                # Threads paralelos
    python3 scraper_gavilanes.py --solo-html                # Solo regenerar HTML
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
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from datetime import datetime

# ──────────────────────────────────────────────
# CONFIGURACIÓN DE NOVELAS
# ──────────────────────────────────────────────
BASE_URL = "https://l.ennovelas-tv.com"

NOVELAS = {
    "gavilanes": {
        "id":       "gavilanes",
        "titulo":   "Pasión de Gavilanes",
        "emoji":    "🦅",
        "año":      "2003–2004",
        "pais":     "🇨🇴 Colombia",
        "genero":   "Drama · Romance",
        "rating":   "7.8",
        "sinopsis": "La historia gira alrededor de los hermanos Reyes: Juan, Óscar y Franco, tres llaneros que llegan a trabajar como peones en la hacienda La Bonita. Allí se enamoran de las tres hijas de Doña Gabriela de Elizondo. Una historia de amor apasionada, traición, venganza y redención.",
        "temporadas": {
            1: {"total": 188, "slug_prefix": "pasion-de-gavilanes-capitulo-",   "slug_suffix": ""},
            2: {"total": 60,  "slug_prefix": "pasion-de-gavilanes-2-capitulo-", "slug_suffix": ""},
        },
    },
    "bella": {
        "id":       "bella",
        "titulo":   "Bella Calamidades",
        "emoji":    "💃",
        "año":      "2009",
        "pais":     "🇨🇴 Colombia",
        "genero":   "Comedia · Romance",
        "rating":   "6.2",
        "sinopsis": "Una joven alocada y divertida llega a la ciudad y revoluciona la vida de todos a su alrededor con su carisma y torpeza incomparables. Una comedia romántica llena de enredos y corazón.",
        "temporadas": {
            1: {"total": 140, "slug_prefix": "bella-calamidades-capitulo-", "slug_suffix": "",
                "overrides": {140: "bella-calamidades-capitulo-140-final"}},
        },
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": BASE_URL + "/",
}

OUTPUT_DIR  = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR  = os.path.join(OUTPUT_DIR, "backups")
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "reproductor_gavilanes.html")

DELAY_MIN   = 1.0
DELAY_MAX   = 2.5
MAX_RETRIES = 3
TIMEOUT     = 25


def json_path(novela_id):
    return os.path.join(OUTPUT_DIR, f"capitulos_{novela_id}.json")


# ──────────────────────────────────────────────
# BACKUP
# ──────────────────────────────────────────────

def make_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    backed  = []
    targets = [OUTPUT_HTML] + [json_path(n) for n in NOVELAS if os.path.exists(json_path(n))]
    for src in targets:
        if os.path.exists(src):
            ext  = os.path.splitext(src)[1]
            name = os.path.splitext(os.path.basename(src))[0]
            dst  = os.path.join(BACKUP_DIR, f"{name}_{stamp}{ext}")
            shutil.copy2(src, dst)
            backed.append(dst)
    if backed:
        print(f"💾 Backup guardado: backups/{stamp} ({len(backed)} archivos)")
    # Rotar: conservar últimos 5 de cada tipo
    for prefix in ["reproductor_gavilanes", "capitulos_gavilanes", "capitulos_bella"]:
        files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(prefix + "_"))
        for old in files[:-5]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except Exception:
                pass


# ──────────────────────────────────────────────
# EXTRACCIÓN
# ──────────────────────────────────────────────

def get_episode_url(novela_id, cap_num, temporada=1):
    cfg  = NOVELAS[novela_id]["temporadas"][temporada]
    ov   = cfg.get("overrides", {})
    if cap_num in ov:
        slug = ov[cap_num]
    else:
        slug = f"{cfg['slug_prefix']}{cap_num}{cfg.get('slug_suffix', '')}"
    return f"{BASE_URL}/{slug}/"


def decode_post_token(token):
    try:
        padded  = token + "=" * (4 - len(token) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}


def extract_player_link(soup):
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(strip=True).lower()
        if "enn.php" in href or "ver capitulo" in text or "ver_capitulo" in text:
            return href
    return None


def extract_iframes_from_page(url, session):
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return {"error": str(e)}

    soup    = BeautifulSoup(r.text, "html.parser")
    servers = {}

    for li in soup.select("ul.serversList li[data-server]"):
        raw   = li.get("data-server", "")
        name  = (li.get_text(strip=True).split() or ["server"])[0].lower()
        inner = BeautifulSoup(raw, "html.parser")
        iframe = inner.find("iframe")
        if iframe and iframe.get("src"):
            servers[name] = iframe["src"]

    if not servers:
        for iframe in soup.select(".watch iframe, .getEmbed iframe, .serverWatch iframe"):
            src = iframe.get("src", "")
            if src:
                name = "vk" if "vk.com" in src else \
                       "vidspeeds" if "vidspeeds" in src else \
                       "uqload" if "uqload" in src else "embed"
                servers[name] = src

    if not servers and "post=" in url:
        token = url.split("post=")[-1].split("&")[0]
        data  = decode_post_token(token)
        if data:
            servers = data

    return servers


def scrape_episode(novela_id, cap_num, temporada, session):
    ep_url = get_episode_url(novela_id, cap_num, temporada)
    result = {
        "novela":     novela_id,
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

    soup       = BeautifulSoup(r.text, "html.parser")
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
# RUNNER
# ──────────────────────────────────────────────

def run_scraper(novela_id, cap_list, temporada, workers=3):
    results = []
    total   = len(cap_list)
    titulo  = NOVELAS[novela_id]["titulo"]
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"\n📺 [{titulo}] Scrapeando {total} capítulos — Temporada {temporada}")
    print(f"   Workers: {workers} | Delay: {DELAY_MIN}-{DELAY_MAX}s\n")

    def scrape_with_delay(cap_num):
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        return scrape_episode(novela_id, cap_num, temporada, session)

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
            print(f"  {status} Cap {cap:3d} [{completed:3d}/{total}] → {servers or data.get('error','sin datos')}")

    results.sort(key=lambda x: x["capitulo"])
    return results


def parse_capitulos_arg(arg, max_cap):
    caps = set()
    for part in arg.split(","):
        part = part.strip()
        if "-" in part:
            s, e = part.split("-", 1)
            caps.update(range(int(s), int(e) + 1))
        else:
            caps.add(int(part))
    return sorted(c for c in caps if 1 <= c <= max_cap)


def scrape_novela(novela_id, cap_arg, temporada, workers, no_backup=False):
    """Scrapea una novela completa y retorna la lista de episodios actualizada."""
    cfg     = NOVELAS[novela_id]
    max_cap = cfg["temporadas"][temporada]["total"]
    out_json = json_path(novela_id)

    if cap_arg:
        cap_list = parse_capitulos_arg(cap_arg, max_cap)
    else:
        cap_list = list(range(1, max_cap + 1))

    # Caché existente
    existing = {}
    if os.path.exists(out_json):
        try:
            with open(out_json, encoding="utf-8") as f:
                prev = json.load(f)
            existing = {(e["capitulo"], e["temporada"]): e for e in prev}
            print(f"📂 [{cfg['titulo']}] {len(existing)} capítulos en caché")
        except Exception:
            pass

    to_scrape  = [c for c in cap_list if (c, temporada) not in existing or not existing[(c, temporada)].get("ok")]
    already_ok = [c for c in cap_list if (c, temporada) in existing and existing[(c, temporada)].get("ok")]

    if already_ok:
        print(f"⏭️  [{cfg['titulo']}] Saltando {len(already_ok)} capítulos ya en caché")
    if to_scrape:
        new_results = run_scraper(novela_id, to_scrape, temporada, workers)
        for r in new_results:
            existing[(r["capitulo"], r["temporada"])] = r
    else:
        print(f"✅ [{cfg['titulo']}] Todos en caché")

    all_eps = sorted(existing.values(), key=lambda x: (x["temporada"], x["capitulo"]))
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(all_eps, f, ensure_ascii=False, indent=2)

    ok_count = sum(1 for e in all_eps if e.get("ok"))
    print(f"💾 [{cfg['titulo']}] {ok_count}/{len(all_eps)} con video → {out_json}")
    return all_eps


# ──────────────────────────────────────────────
# HTML GENERATOR — REPRODUCTOR MULTI-NOVELA
# ──────────────────────────────────────────────

def generate_html(novelas_data):
    """
    novelas_data: dict { novela_id: [lista de episodios] }
    """
    # Metadatos de cada novela para el JS
    novelas_meta = {}
    for nid, eps in novelas_data.items():
        meta = NOVELAS[nid].copy()
        meta.pop("temporadas", None)
        novelas_meta[nid] = meta

    clean_eps = {}
    for nid, eps in novelas_data.items():
        clean_eps[nid] = [
            {
                "novela":     e["novela"],
                "capitulo":   e["capitulo"],
                "temporada":  e["temporada"],
                "servidores": e.get("servidores", {}),
                "ok":         e.get("ok", False),
            }
            for e in eps
        ]

    meta_json = json.dumps(novelas_meta, ensure_ascii=False, separators=(',', ':'))
    eps_json  = json.dumps(clean_eps,   ensure_ascii=False, separators=(',', ':'))

    return HTML_TEMPLATE \
        .replace("NOVELAS_META_PLACEHOLDER", meta_json) \
        .replace("EPISODES_DATA_PLACEHOLDER", eps_json)


# ──────────────────────────────────────────────
# HTML TEMPLATE
# ──────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📺 Mis Novelas — Reproductor</title>
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
    --text:        #eae6da;
    --muted:       #6a6a88;
    --success:     #3ecf8e;
    --player-bg:   #000;
    --sidebar-w:   310px;
    --header-h:    66px;
    --novela-bar:  52px;
  }

  *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }
  html { scroll-behavior: smooth; }
  body { background:var(--bg); color:var(--text); font-family:'DM Sans',sans-serif; min-height:100vh; overflow-x:hidden; }
  ::-webkit-scrollbar { width:5px; height:5px; }
  ::-webkit-scrollbar-track { background:transparent; }
  ::-webkit-scrollbar-thumb { background:var(--border); border-radius:4px; }

  /* ─ TOAST ─ */
  .toast { position:fixed; bottom:24px; right:24px; background:var(--surface2); border:1px solid var(--border); border-radius:10px; padding:11px 16px; font-size:.8rem; color:var(--text); z-index:9999; box-shadow:0 8px 32px rgba(0,0,0,.5); display:flex; align-items:center; gap:9px; transform:translateY(80px); opacity:0; transition:all .3s cubic-bezier(.34,1.56,.64,1); pointer-events:none; }
  .toast.show { transform:translateY(0); opacity:1; }
  .toast.ok   { border-color:var(--success); }

  /* ─ HEADER ─ */
  header { height:var(--header-h); background:rgba(9,9,15,.92); border-bottom:1px solid var(--border); padding:0 24px; display:flex; align-items:center; gap:14px; position:sticky; top:0; z-index:200; backdrop-filter:blur(20px); }
  .logo { font-family:'Playfair Display',serif; font-size:1.3rem; font-weight:900; color:var(--accent); flex-shrink:0; text-shadow:0 0 30px var(--accent-glow); }
  .logo span { color:var(--red); }
  .header-spacer { flex:1; }
  .header-pill { display:flex; align-items:center; gap:7px; background:var(--surface2); border:1px solid var(--border); border-radius:20px; padding:5px 12px; font-size:.74rem; color:var(--muted); }
  .header-pill .dot { width:7px; height:7px; border-radius:50%; background:var(--success); box-shadow:0 0 6px var(--success); animation:pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.5;transform:scale(.85)} }
  .btn-hdr { display:flex; align-items:center; gap:6px; padding:6px 13px; border-radius:8px; border:1px solid var(--border); background:var(--surface2); color:var(--text); font-family:inherit; font-size:.76rem; cursor:pointer; transition:all .18s; }
  .btn-hdr:hover { border-color:var(--accent); color:var(--accent); }

  /* ─ NOVELA SWITCHER BAR ─ */
  .novela-bar { height:var(--novela-bar); background:var(--surface); border-bottom:1px solid var(--border); display:flex; align-items:center; padding:0 16px; gap:8px; overflow-x:auto; scrollbar-width:none; }
  .novela-bar::-webkit-scrollbar { display:none; }
  .novela-tab { display:flex; align-items:center; gap:8px; padding:8px 18px; border-radius:10px; border:1px solid transparent; cursor:pointer; font-family:inherit; font-size:.82rem; font-weight:500; color:var(--muted); background:transparent; transition:all .18s; white-space:nowrap; flex-shrink:0; }
  .novela-tab:hover { background:var(--surface2); color:var(--text); border-color:var(--border); }
  .novela-tab.active { background:linear-gradient(135deg,rgba(200,163,85,.15),rgba(139,26,26,.1)); border-color:rgba(200,163,85,.5); color:var(--accent); font-weight:600; }
  .novela-tab .tab-emoji { font-size:1.1rem; }
  .novela-tab .tab-count { font-size:.67rem; background:var(--surface3); border:1px solid var(--border); border-radius:10px; padding:2px 7px; color:var(--muted); }
  .novela-tab.active .tab-count { background:rgba(200,163,85,.15); border-color:rgba(200,163,85,.3); color:var(--accent); }

  /* ─ LAYOUT ─ */
  .app { display:grid; grid-template-columns:var(--sidebar-w) 1fr; min-height:calc(100vh - var(--header-h) - var(--novela-bar)); }

  /* ─ SIDEBAR ─ */
  aside { background:var(--surface); border-right:1px solid var(--border); display:flex; flex-direction:column; position:sticky; top:calc(var(--header-h) + var(--novela-bar)); height:calc(100vh - var(--header-h) - var(--novela-bar)); overflow:hidden; }
  .sidebar-head { padding:12px 14px; border-bottom:1px solid var(--border); display:flex; flex-direction:column; gap:9px; }
  .sidebar-title { font-size:.67rem; text-transform:uppercase; letter-spacing:.14em; color:var(--muted); font-weight:500; }
  .search-wrap { position:relative; }
  .search-wrap svg { position:absolute; left:9px; top:50%; transform:translateY(-50%); width:13px; height:13px; color:var(--muted); pointer-events:none; }
  .search-wrap input { width:100%; background:var(--surface2); border:1px solid var(--border); border-radius:8px; padding:7px 10px 7px 30px; color:var(--text); font-family:inherit; font-size:.8rem; outline:none; transition:border-color .2s; }
  .search-wrap input:focus { border-color:var(--accent); }
  .search-wrap input::placeholder { color:var(--muted); }
  .filter-tabs { display:flex; gap:4px; }
  .filter-tab { flex:1; padding:5px 3px; text-align:center; background:var(--surface2); border:1px solid var(--border); border-radius:6px; cursor:pointer; font-size:.68rem; color:var(--muted); font-family:inherit; transition:all .15s; white-space:nowrap; }
  .filter-tab.active { background:var(--accent); color:#000; border-color:var(--accent); font-weight:600; }
  .season-tabs { display:flex; gap:5px; padding:8px 14px; border-bottom:1px solid var(--border); }
  .season-tab { flex:1; padding:5px; text-align:center; background:var(--surface2); border:1px solid var(--border); border-radius:6px; cursor:pointer; font-size:.72rem; color:var(--muted); font-family:inherit; transition:all .15s; }
  .season-tab.active { background:var(--accent); color:#000; border-color:var(--accent); font-weight:600; }
  .ep-list { overflow-y:auto; flex:1; padding:5px; }
  .ep-item { display:flex; align-items:center; gap:8px; padding:7px 9px; border-radius:8px; cursor:pointer; transition:all .15s; border:1px solid transparent; margin-bottom:2px; position:relative; }
  .ep-item:hover { background:var(--surface2); border-color:var(--border); }
  .ep-item.active { background:linear-gradient(135deg,rgba(200,163,85,.12),rgba(139,26,26,.08)); border-color:rgba(200,163,85,.5); }
  .ep-item.no-data { opacity:.3; cursor:default; }
  .ep-item.no-data:hover { background:transparent; border-color:transparent; }
  .ep-item.watched:not(.active)::after { content:'✓'; position:absolute; right:9px; top:50%; transform:translateY(-50%); font-size:.68rem; color:var(--success); font-weight:700; }
  .ep-num { font-family:'Playfair Display',serif; font-size:.93rem; font-weight:700; color:var(--accent); min-width:32px; text-align:center; opacity:.75; }
  .ep-item.active .ep-num { opacity:1; }
  .ep-item.watched .ep-num { color:var(--success); }
  .ep-meta { flex:1; min-width:0; }
  .ep-title { font-size:.77rem; font-weight:500; line-height:1.3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .ep-sub { font-size:.65rem; color:var(--muted); margin-top:2px; }
  .ep-sub .wb { color:var(--success); font-weight:600; }
  .ep-dot { width:6px; height:6px; border-radius:50%; background:var(--border); flex-shrink:0; }
  .ep-item.active .ep-dot { background:var(--accent); }
  .ep-item.watched .ep-dot { background:var(--success); }

  /* ─ MAIN ─ */
  main { display:flex; flex-direction:column; overflow:hidden; }
  .player-wrap { background:var(--player-bg); position:relative; padding-top:56.25%; flex-shrink:0; }
  .player-wrap iframe { position:absolute; inset:0; width:100%; height:100%; border:none; }
  .player-placeholder { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px; background:radial-gradient(ellipse at center,#100a08 0%,#09090f 70%); }
  .placeholder-emoji { font-size:5rem; opacity:.15; animation:floatIcon 4s ease-in-out infinite; }
  @keyframes floatIcon { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-10px)} }
  .placeholder-text { font-size:.9rem; color:var(--muted); }

  /* ─ PLAYER INFO ─ */
  .player-info { padding:14px 22px; border-bottom:1px solid var(--border); display:flex; align-items:center; flex-wrap:wrap; gap:10px; background:var(--surface); }
  .current-ep-info { flex:1; }
  .current-ep-label { font-size:.67rem; text-transform:uppercase; letter-spacing:.1em; color:var(--muted); }
  .current-ep-title { font-family:'Playfair Display',serif; font-size:1.15rem; font-weight:700; line-height:1.2; }
  .progress-wrap { width:100%; height:3px; background:var(--border); border-radius:2px; margin-top:7px; overflow:hidden; }
  .progress-fill { height:100%; background:linear-gradient(90deg,var(--accent),var(--red)); border-radius:2px; transition:width .4s; }
  .nav-btns { display:flex; gap:6px; }
  .nav-btn { padding:7px 14px; border-radius:8px; border:1px solid var(--border); background:var(--surface2); color:var(--text); cursor:pointer; font-size:.78rem; font-family:inherit; transition:all .15s; display:flex; align-items:center; gap:5px; }
  .nav-btn:hover:not(:disabled) { border-color:var(--accent); color:var(--accent); }
  .nav-btn:disabled { opacity:.25; cursor:default; }
  .btn-mark { padding:7px 12px; border-radius:8px; border:1px solid var(--border); background:var(--surface2); color:var(--muted); cursor:pointer; font-size:.76rem; font-family:inherit; transition:all .15s; }
  .btn-mark:hover { border-color:var(--success); color:var(--success); }
  .btn-mark.marked { border-color:var(--success); color:var(--success); background:rgba(62,207,142,.08); }

  /* ─ SERVER BAR ─ */
  .server-bar { padding:10px 22px; display:flex; align-items:center; gap:7px; flex-wrap:wrap; border-bottom:1px solid var(--border); background:var(--surface); }
  .server-label { font-size:.67rem; text-transform:uppercase; letter-spacing:.12em; color:var(--muted); font-weight:500; }
  .server-btn { padding:4px 12px; border-radius:20px; border:1px solid var(--border); background:var(--surface2); color:var(--text); cursor:pointer; font-size:.73rem; font-family:inherit; transition:all .15s; text-transform:uppercase; letter-spacing:.06em; }
  .server-btn:hover { border-color:var(--accent); }
  .server-btn.active { background:var(--accent); border-color:var(--accent); color:#000; font-weight:600; }
  .no-servers { font-size:.78rem; color:var(--red); }

  /* ─ PANEL TABS ─ */
  .panel-tabs { display:flex; border-bottom:1px solid var(--border); background:var(--surface); }
  .panel-tab { flex:1; padding:11px; text-align:center; font-size:.73rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); cursor:pointer; font-family:inherit; border:none; background:none; transition:all .15s; border-bottom:2px solid transparent; }
  .panel-tab.active { color:var(--accent); border-bottom-color:var(--accent); }
  .panel-tab:hover:not(.active) { color:var(--text); }
  .panel-body { padding:18px 22px; flex:1; overflow-y:auto; }
  .panel-section { display:none; }
  .panel-section.active { display:block; }

  /* ─ ABOUT ─ */
  .about-sub { font-size:.67rem; text-transform:uppercase; letter-spacing:.12em; color:var(--muted); margin-bottom:8px; }
  .about-desc { font-size:.84rem; line-height:1.75; color:rgba(234,230,218,.72); max-width:680px; }
  .badges { display:flex; gap:6px; flex-wrap:wrap; margin-top:12px; }
  .badge { padding:3px 11px; border-radius:20px; font-size:.68rem; background:var(--surface2); border:1px solid var(--border); color:var(--muted); }
  .stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(100px,1fr)); gap:10px; margin-top:18px; }
  .stat-card { background:var(--surface2); border:1px solid var(--border); border-radius:10px; padding:13px 10px; text-align:center; }
  .stat-num { font-family:'Playfair Display',serif; font-size:1.4rem; color:var(--accent); display:block; }
  .stat-label { font-size:.63rem; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }

  /* ─ HISTORY ─ */
  .history-toolbar { display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; }
  .history-count { font-size:.76rem; color:var(--muted); }
  .btn-clear { padding:4px 11px; border-radius:7px; border:1px solid var(--border); background:transparent; color:var(--muted); cursor:pointer; font-size:.71rem; font-family:inherit; transition:all .15s; }
  .btn-clear:hover { border-color:var(--red); color:var(--red); }
  .history-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:7px; }
  .history-card { background:var(--surface2); border:1px solid var(--border); border-radius:9px; padding:11px 9px; cursor:pointer; transition:all .15s; text-align:center; position:relative; }
  .history-card:hover { border-color:var(--accent); transform:translateY(-2px); }
  .history-card .h-emoji { display:block; font-size:1rem; margin-bottom:3px; }
  .history-card .h-num { font-family:'Playfair Display',serif; font-size:1.3rem; color:var(--accent); display:block; }
  .history-card .h-label { font-size:.67rem; color:var(--muted); }
  .history-card .h-date { font-size:.6rem; color:var(--border); margin-top:3px; }
  .history-card .h-rm { position:absolute; top:5px; right:7px; font-size:.63rem; color:var(--border); cursor:pointer; padding:2px; transition:color .15s; }
  .history-card .h-rm:hover { color:var(--red); }

  /* ─ BACKUP ─ */
  .backup-zone { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:18px; }
  .bbtn { display:flex; flex-direction:column; align-items:center; gap:7px; padding:20px 14px; border-radius:12px; border:1px solid var(--border); background:var(--surface2); cursor:pointer; transition:all .2s; font-family:inherit; color:var(--text); }
  .bbtn:hover { border-color:var(--accent); background:var(--surface3); transform:translateY(-2px); }
  .bbtn svg { width:24px; height:24px; color:var(--accent); }
  .bbtn .bb-t { font-size:.82rem; font-weight:600; }
  .bbtn .bb-s { font-size:.68rem; color:var(--muted); }
  .backup-log-title { font-size:.67rem; text-transform:uppercase; letter-spacing:.12em; color:var(--muted); margin-bottom:7px; }
  .backup-log { background:var(--surface2); border:1px solid var(--border); border-radius:9px; padding:12px; max-height:180px; overflow-y:auto; font-size:.73rem; color:var(--muted); line-height:1.7; font-family:monospace; }
  .log-line { color:var(--text); }
  .log-line.ok { color:var(--success); }
  .log-line.warn { color:var(--accent); }

  /* ─ EMPTY ─ */
  .empty-state { text-align:center; padding:36px 20px; color:var(--muted); font-size:.83rem; }
  .empty-icon { font-size:2.4rem; display:block; margin-bottom:9px; opacity:.3; }

  /* ─ RESPONSIVE ─ */
  @media (max-width:820px) {
    :root { --sidebar-w:100%; }
    .app { grid-template-columns:1fr; }
    aside { position:relative; top:0; height:36vh; border-right:none; border-bottom:1px solid var(--border); }
    .backup-zone { grid-template-columns:1fr; }
    header { padding:0 14px; gap:8px; }
    .btn-hdr .lbl { display:none; }
  }
</style>
</head>
<body>

<div class="toast" id="toast"></div>

<!-- HEADER -->
<header>
  <div class="logo">📺 Mis <span>Novelas</span></div>
  <div class="header-spacer"></div>
  <div class="header-pill"><div class="dot"></div><span id="headerStat">Cargando...</span></div>
  <button class="btn-hdr" onclick="quickBackup()">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7,10 12,15 17,10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
    <span class="lbl">Backup</span>
  </button>
</header>

<!-- NOVELA SWITCHER -->
<div class="novela-bar" id="novelaBar"></div>

<!-- APP -->
<div class="app">

  <!-- SIDEBAR -->
  <aside>
    <div class="sidebar-head">
      <div class="sidebar-title" id="sidebarTitle">📋 Capítulos</div>
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input type="number" id="searchCap" placeholder="Buscar capítulo..." min="1">
      </div>
      <div class="filter-tabs">
        <button class="filter-tab active" data-filter="all"    onclick="setFilter('all')">Todos</button>
        <button class="filter-tab"        data-filter="ok"     onclick="setFilter('ok')">Video</button>
        <button class="filter-tab"        data-filter="seen"   onclick="setFilter('seen')">Vistos</button>
        <button class="filter-tab"        data-filter="unseen" onclick="setFilter('unseen')">Pend.</button>
      </div>
    </div>
    <div class="season-tabs" id="seasonTabs"></div>
    <div class="ep-list" id="epList"></div>
  </aside>

  <!-- MAIN -->
  <main>
    <div class="player-wrap" id="playerWrap">
      <div class="player-placeholder" id="playerPh">
        <div class="placeholder-emoji" id="phEmoji">📺</div>
        <div class="placeholder-text">Selecciona un capítulo para comenzar</div>
      </div>
    </div>

    <div class="player-info">
      <div class="current-ep-info">
        <div class="current-ep-label">Reproduciendo ahora</div>
        <div class="current-ep-title" id="currentTitle">—</div>
        <div class="progress-wrap" id="progressWrap" style="display:none">
          <div class="progress-fill" id="progressFill"></div>
        </div>
      </div>
      <button class="btn-mark" id="btnMark" onclick="toggleWatched()" style="display:none">✓ Marcar visto</button>
      <div class="nav-btns">
        <button class="nav-btn" id="prevBtn" onclick="navigate(-1)" disabled>← Ant.</button>
        <button class="nav-btn" id="nextBtn" onclick="navigate(1)"  disabled>Sig. →</button>
      </div>
    </div>

    <div class="server-bar" id="serverBar">
      <span class="server-label">Servidor:</span>
      <span class="no-servers" id="noServersMsg" style="display:none">Sin servidores</span>
    </div>

    <div class="panel-tabs">
      <button class="panel-tab active" data-panel="about"   onclick="switchPanel('about')">ℹ️ Serie</button>
      <button class="panel-tab"        data-panel="history" onclick="switchPanel('history')">🕐 Historial</button>
      <button class="panel-tab"        data-panel="backup"  onclick="switchPanel('backup')">💾 Backup</button>
    </div>

    <div class="panel-body">
      <div class="panel-section active" id="panel-about">
        <div class="about-sub" id="aboutTitle">Sinopsis</div>
        <div class="about-desc" id="aboutDesc">—</div>
        <div class="badges" id="aboutBadges"></div>
        <div class="stats-grid" id="statsGrid"></div>
      </div>

      <div class="panel-section" id="panel-history">
        <div class="history-toolbar">
          <span class="history-count" id="historyCount">0 vistos</span>
          <button class="btn-clear" onclick="clearHistory()">🗑 Limpiar</button>
        </div>
        <div class="history-grid" id="historyGrid"></div>
      </div>

      <div class="panel-section" id="panel-backup">
        <div class="backup-zone">
          <button class="bbtn" onclick="exportBackup()">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7,10 12,15 17,10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            <span class="bb-t">Exportar backup</span>
            <span class="bb-s">Descarga historial JSON</span>
          </button>
          <button class="bbtn" onclick="document.getElementById('importFile').click()">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><line x1="12" y1="3" x2="12" y2="15"/><polyline points="17,10 12,15 7,10" transform="rotate(180,12,12.5)"/></svg>
            <span class="bb-t">Importar backup</span>
            <span class="bb-s">Restaura tu historial</span>
          </button>
          <input type="file" id="importFile" accept=".json" style="display:none" onchange="importBackup(event)">
        </div>
        <div class="backup-log-title">📋 Registro de actividad</div>
        <div class="backup-log" id="backupLog"><span class="log-line warn">» Sistema listo.</span></div>
      </div>
    </div>
  </main>
</div>

<script>
// ═══════════════════════════════
// DATA
// ═══════════════════════════════
const NOVELAS_META = NOVELAS_META_PLACEHOLDER;
const EPISODES     = EPISODES_DATA_PLACEHOLDER;

// ═══════════════════════════════
// STATE
// ═══════════════════════════════
let activeNovela = Object.keys(NOVELAS_META)[0];
let activeSeason = 1;
let activeFilter = 'all';
let currentEp    = null;

const STORAGE_KEY = 'misnovelas_history_v1';
const PREFS_KEY   = 'misnovelas_prefs_v1';

const loadHistory = () => { try { return JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}'); } catch(e){ return {}; } };
const saveHistory = h => localStorage.setItem(STORAGE_KEY, JSON.stringify(h));
const loadPrefs   = () => { try { return JSON.parse(localStorage.getItem(PREFS_KEY)||'{}'); } catch(e){ return {}; } };
const savePrefs   = p => localStorage.setItem(PREFS_KEY, JSON.stringify(p));

const hasVideo = ep => ep.servidores && Object.keys(ep.servidores).length > 0;
const epKey    = ep => `${ep.novela}_t${ep.temporada}c${ep.capitulo}`;

function currentData()  { return EPISODES[activeNovela] || []; }
function currentSeasons(){ return [...new Set(currentData().map(e=>e.temporada))].sort(); }

// ═══════════════════════════════
// INIT
// ═══════════════════════════════
function init() {
  buildNovelaTabs();
  const p = loadPrefs();
  activeNovela = p.lastNovela || activeNovela;
  if (!NOVELAS_META[activeNovela]) activeNovela = Object.keys(NOVELAS_META)[0];
  activeSeason = p.lastSeason || 1;
  updateNovelaTabs();
  buildSeasonTabs();
  renderList();
  updateStats();
  renderHistory();
  updateAbout();
  logBackup('» Sistema iniciado.','warn');
  autoResume();
}

// ═══════════════════════════════
// NOVELA TABS
// ═══════════════════════════════
function buildNovelaTabs() {
  const bar = document.getElementById('novelaBar');
  bar.innerHTML = Object.entries(NOVELAS_META).map(([id, m]) => {
    const eps = EPISODES[id] || [];
    const ok  = eps.filter(hasVideo).length;
    return `<button class="novela-tab" data-novela="${id}" onclick="switchNovela('${id}')">
      <span class="tab-emoji">${m.emoji}</span>
      <span>${m.titulo}</span>
      <span class="tab-count">${ok} caps</span>
    </button>`;
  }).join('');
}

function switchNovela(id) {
  activeNovela = id;
  activeSeason = currentSeasons()[0] || 1;
  activeFilter = 'all';
  document.querySelectorAll('.filter-tab').forEach(t => t.classList.toggle('active', t.dataset.filter==='all'));
  updateNovelaTabs();
  buildSeasonTabs();
  renderList();
  updateStats();
  updateAbout();
  savePrefs(Object.assign(loadPrefs(),{lastNovela:id, lastSeason:activeSeason}));
  // Reset player placeholder emoji
  const ph = document.getElementById('phEmoji');
  if (ph) ph.textContent = NOVELAS_META[id]?.emoji || '📺';
}

function updateNovelaTabs() {
  document.querySelectorAll('.novela-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.novela === activeNovela));
}

// ═══════════════════════════════
// SEASON TABS
// ═══════════════════════════════
function buildSeasonTabs() {
  const seasons = currentSeasons();
  const el = document.getElementById('seasonTabs');
  el.innerHTML = seasons.map(s =>
    `<button class="season-tab" data-season="${s}" onclick="switchSeason(${s})">T${s}</button>`
  ).join('');
  el.style.display = seasons.length > 1 ? '' : 'none';
  if (!seasons.includes(activeSeason)) activeSeason = seasons[0];
  updateSeasonTabs();
}

function switchSeason(s) {
  activeSeason = s;
  updateSeasonTabs();
  renderList();
  savePrefs(Object.assign(loadPrefs(),{lastSeason:s}));
}

function updateSeasonTabs() {
  document.querySelectorAll('.season-tab').forEach(t =>
    t.classList.toggle('active', parseInt(t.dataset.season) === activeSeason));
}

// ═══════════════════════════════
// FILTERS
// ═══════════════════════════════
function setFilter(f) {
  activeFilter = f;
  document.querySelectorAll('.filter-tab').forEach(t => t.classList.toggle('active', t.dataset.filter===f));
  renderList();
}

function applyFilter(eps) {
  const h = loadHistory();
  if (activeFilter==='ok')     return eps.filter(hasVideo);
  if (activeFilter==='seen')   return eps.filter(e=>h[epKey(e)]);
  if (activeFilter==='unseen') return eps.filter(e=>hasVideo(e)&&!h[epKey(e)]);
  return eps;
}

// ═══════════════════════════════
// RENDER LIST
// ═══════════════════════════════
function renderList(search) {
  if (search===undefined) search = document.getElementById('searchCap').value||'';
  const h   = loadHistory();
  let eps   = currentData().filter(e=>e.temporada===activeSeason);
  eps = applyFilter(eps);
  if (search) eps = eps.filter(e=>e.capitulo.toString().includes(search));

  const list = document.getElementById('epList');
  if (!eps.length) {
    list.innerHTML = '<div class="empty-state"><span class="empty-icon">📭</span>Sin resultados</div>';
    return;
  }
  list.innerHTML = eps.map(ep => {
    const ok       = hasVideo(ep);
    const isActive = currentEp && currentEp.capitulo===ep.capitulo && currentEp.temporada===ep.temporada && currentEp.novela===ep.novela;
    const watched  = !!h[epKey(ep)];
    const srvStr   = ok ? Object.keys(ep.servidores).join(' · ') : 'sin video';
    const wb       = watched ? ' <span class="wb">· visto</span>' : '';
    return `<div class="ep-item ${!ok?'no-data':''} ${isActive?'active':''} ${watched&&!isActive?'watched':''}"
                 onclick="${ok?`loadEp(${ep.capitulo},${ep.temporada},'${ep.novela}')`:''}"
                 data-cap="${ep.capitulo}" data-season="${ep.temporada}" data-novela="${ep.novela}">
      <div class="ep-num">${ep.capitulo}</div>
      <div class="ep-meta">
        <div class="ep-title">Capítulo ${ep.capitulo}</div>
        <div class="ep-sub">${srvStr}${wb}</div>
      </div>
      <div class="ep-dot"></div>
    </div>`;
  }).join('');
}

// ═══════════════════════════════
// LOAD EPISODE
// ═══════════════════════════════
function loadEp(cap, season, nid) {
  const ep = (EPISODES[nid]||[]).find(e=>e.capitulo===cap&&e.temporada===season);
  if (!ep || !hasVideo(ep)) return;

  // Si cambiamos de novela, actualizar sidebar
  if (nid !== activeNovela) {
    activeNovela = nid;
    activeSeason = season;
    updateNovelaTabs();
    buildSeasonTabs();
    updateAbout();
  }
  currentEp = ep;

  renderList();
  const active = document.querySelector('.ep-item.active');
  if (active) active.scrollIntoView({block:'nearest',behavior:'smooth'});

  const meta = NOVELAS_META[nid];
  document.getElementById('currentTitle').textContent = `${meta.emoji} ${meta.titulo} — T${season} Cap ${cap}`;

  // Progreso
  const valid = (EPISODES[nid]||[]).filter(e=>e.temporada===season&&hasVideo(e));
  const idx   = valid.findIndex(e=>e.capitulo===cap);
  const pct   = valid.length>1 ? Math.round((idx/(valid.length-1))*100) : 0;
  document.getElementById('progressWrap').style.display='';
  document.getElementById('progressFill').style.width=pct+'%';

  // Botón marcar
  const btnMark = document.getElementById('btnMark');
  btnMark.style.display='';
  updateMarkBtn(ep);

  // Servidores
  renderServerBtns(ep);

  // Nav
  document.getElementById('prevBtn').disabled = idx<=0;
  document.getElementById('nextBtn').disabled = idx>=valid.length-1;

  savePrefs(Object.assign(loadPrefs(),{lastNovela:nid, lastSeason:season, lastCap:cap}));
}

function renderServerBtns(ep) {
  const bar  = document.getElementById('serverBar');
  const keys = Object.keys(ep.servidores);
  bar.querySelectorAll('.server-btn').forEach(b=>b.remove());
  document.getElementById('noServersMsg').style.display = keys.length?'none':'';
  keys.forEach((key,i) => {
    const btn = document.createElement('button');
    btn.className = 'server-btn'+(i===0?' active':'');
    btn.textContent = key.toUpperCase();
    btn.onclick = () => {
      bar.querySelectorAll('.server-btn').forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');
      setIframe(ep.servidores[key]);
    };
    bar.appendChild(btn);
  });
  if (keys.length) setIframe(ep.servidores[keys[0]]);
}

function setIframe(src) {
  const wrap = document.getElementById('playerWrap');
  const ph   = document.getElementById('playerPh');
  if (ph) ph.style.display='none';
  wrap.querySelectorAll('iframe').forEach(f=>f.remove());
  const iframe = document.createElement('iframe');
  iframe.src   = src;
  iframe.setAttribute('allowfullscreen','');
  iframe.setAttribute('allow','autoplay; fullscreen');
  iframe.frameBorder='0';
  wrap.appendChild(iframe);
}

function navigate(dir) {
  if (!currentEp) return;
  const valid = (EPISODES[currentEp.novela]||[]).filter(e=>e.temporada===currentEp.temporada&&hasVideo(e));
  const idx   = valid.findIndex(e=>e.capitulo===currentEp.capitulo);
  const next  = valid[idx+dir];
  if (next) loadEp(next.capitulo, next.temporada, next.novela);
}

// ═══════════════════════════════
// WATCH / MARK
// ═══════════════════════════════
function toggleWatched() {
  if (!currentEp) return;
  const h   = loadHistory();
  const key = epKey(currentEp);
  if (h[key]) {
    delete h[key];
    showToast('Desmarcado','👁');
  } else {
    h[key] = {watchedAt:new Date().toISOString(), cap:currentEp.capitulo, season:currentEp.temporada, novela:currentEp.novela};
    showToast(`Cap ${currentEp.capitulo} marcado como visto ✓`,'✅');
  }
  saveHistory(h);
  updateMarkBtn(currentEp);
  renderList();
  renderHistory();
  updateStats();
}

function updateMarkBtn(ep) {
  const h   = loadHistory();
  const btn = document.getElementById('btnMark');
  if (h[epKey(ep)]) { btn.classList.add('marked'); btn.textContent='✓ Visto'; }
  else              { btn.classList.remove('marked'); btn.textContent='✓ Marcar visto'; }
}

// ═══════════════════════════════
// PANELS
// ═══════════════════════════════
function switchPanel(p) {
  document.querySelectorAll('.panel-tab').forEach(t=>t.classList.toggle('active',t.dataset.panel===p));
  document.querySelectorAll('.panel-section').forEach(s=>s.classList.toggle('active',s.id==='panel-'+p));
  if (p==='history') renderHistory();
}

// ═══════════════════════════════
// ABOUT
// ═══════════════════════════════
function updateAbout() {
  const m = NOVELAS_META[activeNovela];
  document.getElementById('aboutTitle').textContent  = `${m.emoji} ${m.titulo}`;
  document.getElementById('aboutDesc').textContent   = m.sinopsis;
  document.getElementById('aboutBadges').innerHTML   = `
    <span class="badge">${m.pais}</span>
    <span class="badge">${m.genero}</span>
    <span class="badge">📅 ${m.año}</span>
    <span class="badge">⭐ ${m.rating}/10</span>
  `;
  document.getElementById('sidebarTitle').textContent = `${m.emoji} ${m.titulo}`;
  updateStats();
}

// ═══════════════════════════════
// STATS
// ═══════════════════════════════
function updateStats() {
  const h       = loadHistory();
  const all     = Object.values(EPISODES).flat();
  const watched = Object.keys(h).length;
  const allOk   = all.filter(hasVideo).length;

  // Header global
  document.getElementById('headerStat').textContent = `${allOk} caps · ${watched} vistos`;

  // Stats panel de la novela activa
  const eps  = currentData();
  const ok   = eps.filter(hasVideo).length;
  const seas = currentSeasons().length;
  const wn   = eps.filter(e=>h[epKey(e)]).length;
  document.getElementById('statsGrid').innerHTML = `
    <div class="stat-card"><span class="stat-num">${eps.length}</span><span class="stat-label">Capítulos</span></div>
    <div class="stat-card"><span class="stat-num">${ok}</span><span class="stat-label">Con video</span></div>
    <div class="stat-card"><span class="stat-num">${wn}</span><span class="stat-label">Vistos</span></div>
    <div class="stat-card"><span class="stat-num">${seas}</span><span class="stat-label">Temporadas</span></div>
    <div class="stat-card"><span class="stat-num">${ok>0?Math.round((wn/ok)*100):0}%</span><span class="stat-label">Progreso</span></div>
  `;
}

// ═══════════════════════════════
// HISTORY
// ═══════════════════════════════
function renderHistory() {
  const h     = loadHistory();
  const items = Object.values(h).sort((a,b)=>new Date(b.watchedAt)-new Date(a.watchedAt));
  const grid  = document.getElementById('historyGrid');
  document.getElementById('historyCount').textContent = `${items.length} visto${items.length!==1?'s':''}`;
  if (!items.length) {
    grid.innerHTML='<div class="empty-state" style="grid-column:1/-1"><span class="empty-icon">📺</span>Aún no has marcado capítulos.</div>';
    return;
  }
  grid.innerHTML = items.map(item => {
    const d   = new Date(item.watchedAt);
    const fmt = d.toLocaleDateString('es-ES',{day:'2-digit',month:'short'});
    const key = `${item.novela}_t${item.season}c${item.cap}`;
    const m   = NOVELAS_META[item.novela] || {};
    return `<div class="history-card" onclick="loadEp(${item.cap},${item.season},'${item.novela}')">
      <span class="h-rm" onclick="event.stopPropagation();removeFromHistory('${key}')">✕</span>
      <span class="h-emoji">${m.emoji||'📺'}</span>
      <span class="h-num">${item.cap}</span>
      <span class="h-label">T${item.season} · ${m.titulo||item.novela}</span>
      <div class="h-date">${fmt}</div>
    </div>`;
  }).join('');
}

function removeFromHistory(key) {
  const h = loadHistory(); delete h[key]; saveHistory(h);
  renderHistory(); updateStats(); renderList();
  if (currentEp && key===epKey(currentEp)) updateMarkBtn(currentEp);
  showToast('Eliminado','🗑');
}

function clearHistory() {
  if (!confirm('¿Limpiar todo el historial?')) return;
  saveHistory({}); renderHistory(); updateStats(); renderList();
  if (currentEp) updateMarkBtn(currentEp);
  showToast('Historial limpiado','🗑');
  logBackup('» Historial limpiado.','warn');
}

// ═══════════════════════════════
// BACKUP
// ═══════════════════════════════
function exportBackup() {
  const payload = {version:2, exportedAt:new Date().toISOString(), history:loadHistory(), prefs:loadPrefs()};
  const blob    = new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
  const url     = URL.createObjectURL(blob);
  const a       = Object.assign(document.createElement('a'),{href:url,download:`misnovelas_backup_${new Date().toISOString().slice(0,10)}.json`});
  a.click(); URL.revokeObjectURL(url);
  showToast('Backup exportado','💾');
  logBackup(`✓ Exportado — ${Object.keys(loadHistory()).length} registros`,'ok');
}

function importBackup(event) {
  const file = event.target.files[0]; if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    try {
      const data = JSON.parse(e.target.result);
      if (!data.history) throw new Error('Formato inválido');
      saveHistory(Object.assign(loadHistory(), data.history));
      if (data.prefs) savePrefs(Object.assign(loadPrefs(), data.prefs));
      renderHistory(); updateStats(); renderList();
      if (currentEp) updateMarkBtn(currentEp);
      showToast(`Importado: ${Object.keys(data.history).length} registros`,'✅');
      logBackup(`✓ Importado — ${Object.keys(data.history).length} registros`,'ok');
    } catch(err) {
      showToast('Error: '+err.message,'❌');
      logBackup('✗ Error al importar: '+err.message);
    }
  };
  reader.readAsText(file);
  event.target.value='';
}

function quickBackup() { exportBackup(); }

function logBackup(msg,cls) {
  const log  = document.getElementById('backupLog');
  const line = document.createElement('div');
  line.className = 'log-line'+(cls?' '+cls:'');
  const t = new Date().toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  line.textContent=`[${t}] ${msg}`;
  log.appendChild(line); log.scrollTop=log.scrollHeight;
}

// ═══════════════════════════════
// TOAST
// ═══════════════════════════════
let _tt=null;
function showToast(msg,icon) {
  const el=document.getElementById('toast');
  el.innerHTML=(icon?icon+' ':'')+msg;
  el.className='toast show'+(icon==='✅'||icon==='💾'?' ok':'');
  clearTimeout(_tt); _tt=setTimeout(()=>el.className='toast',3000);
}

// ═══════════════════════════════
// SEARCH
// ═══════════════════════════════
document.getElementById('searchCap').addEventListener('input', e => renderList(e.target.value||''));
document.getElementById('searchCap').addEventListener('keydown', e => {
  if (e.key==='Enter') {
    const v = parseInt(e.target.value);
    if (v) {
      const ep = currentData().find(d=>d.capitulo===v&&d.temporada===activeSeason);
      if (ep&&hasVideo(ep)) loadEp(v,activeSeason,activeNovela);
    }
  }
});

// ═══════════════════════════════
// AUTO-RESUME
// ═══════════════════════════════
function autoResume() {
  const p = loadPrefs();
  if (p.lastCap && p.lastNovela) {
    const ep = (EPISODES[p.lastNovela]||[]).find(e=>e.capitulo===p.lastCap&&e.temporada===p.lastSeason);
    if (ep && hasVideo(ep)) {
      const m = NOVELAS_META[p.lastNovela]||{};
      showToast(`${m.emoji||''} Último visto: T${p.lastSeason} Cap ${p.lastCap} — clic para continuar`,'▶');
      setTimeout(() => {
        const el = document.querySelector(`[data-cap="${p.lastCap}"][data-novela="${p.lastNovela}"]`);
        if (el) el.scrollIntoView({block:'center',behavior:'smooth'});
      }, 600);
    }
  }
}

init();
</script>
</body>
</html>
"""


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scraper Multi-Novela → HTML")
    parser.add_argument("--novela",    type=str, default=None, choices=list(NOVELAS.keys()),
                        help="Novela a scrapear (default: todas)")
    parser.add_argument("--capitulos", type=str, default=None)
    parser.add_argument("--temporada", type=int, default=1)
    parser.add_argument("--workers",   type=int, default=3)
    parser.add_argument("--solo-html", action="store_true")
    parser.add_argument("--output",    type=str, default=OUTPUT_HTML)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    if not args.no_backup:
        make_backup()

    # ── Solo regenerar HTML
    if args.solo_html:
        novelas_data = {}
        for nid in NOVELAS:
            jf = json_path(nid)
            if os.path.exists(jf):
                with open(jf, encoding="utf-8") as f:
                    novelas_data[nid] = json.load(f)
        if not novelas_data:
            print("❌ No hay JSONs. Ejecuta el scraper primero.")
            sys.exit(1)
        html = generate_html(novelas_data)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ HTML regenerado → {args.output}")
        return

    # ── Determinar qué novelas scrapear
    novelas_to_scrape = [args.novela] if args.novela else list(NOVELAS.keys())

    novelas_data = {}
    for nid in novelas_to_scrape:
        temporada = args.temporada
        if temporada not in NOVELAS[nid]["temporadas"]:
            temporada = 1
        eps = scrape_novela(nid, args.capitulos, temporada, args.workers, args.no_backup)
        novelas_data[nid] = eps

    # Para novelas no scrapeadas en este run, cargar JSON existente
    for nid in NOVELAS:
        if nid not in novelas_data:
            jf = json_path(nid)
            if os.path.exists(jf):
                with open(jf, encoding="utf-8") as f:
                    novelas_data[nid] = json.load(f)

    # Generar HTML unificado
    html = generate_html(novelas_data)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    total  = sum(len(v) for v in novelas_data.values())
    ok_tot = sum(1 for v in novelas_data.values() for e in v if e.get("ok"))
    print(f"\n🎬 HTML generado → {args.output}")
    print(f"📊 {ok_tot}/{total} capítulos con video en {len(novelas_data)} novelas\n")


if __name__ == "__main__":
    main()
