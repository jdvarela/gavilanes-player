import json, re

with open("series_data.json") as f:
    series_data = json.load(f)

# ── EPISODE DATA FROM EXISTING FILE ──────────────────────────────
with open("capitulos_gavilanes.json") as f:
    gav_raw = json.load(f)
with open("capitulos_bella.json") as f:
    bella_raw = json.load(f)

# Clean gavilanes episodes
gav_eps = []
for ep in gav_raw:
    if isinstance(ep, dict) and ep.get("servidores"):
        ep["novela"] = "gavilanes"
        ep["titulo"] = ep.get("titulo", f"Capítulo {ep['capitulo']}")
        gav_eps.append(ep)

# Clean bella episodes
bella_eps = []
for ep in bella_raw:
    if isinstance(ep, dict) and ep.get("servidores"):
        ep["novela"] = "bella"
        ep["titulo"] = ep.get("titulo", f"Capítulo {ep['capitulo']}")
        bella_eps.append(ep)

sq_eps = series_data["scream_queens"]
rv_eps = series_data["riverdale"]

all_episodes = {
    "gavilanes": gav_eps,
    "bella": bella_eps,
    "scream_queens": sq_eps,
    "riverdale": rv_eps
}

print(f"Gavilanes: {len(gav_eps)}, Bella: {len(bella_eps)}, SQ: {len(sq_eps)}, RV: {len(rv_eps)}")
print("Total:", sum(len(v) for v in all_episodes.values()))

# Output as JS-ready JSON
out = {}
for k,v in all_episodes.items():
    out[k] = v

with open("all_episodes.json","w",encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print("Saved all_episodes.json")
