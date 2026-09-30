# %% [markdown]
# # SDSC 2026 — Presenter runbook
#
# Open this notebook in **VS Code** next to **Copilot Chat (agent mode, Claude model)**.
# For every live step it gives you: the **prompt** to paste, the **tool calls** to expect, the
# **talking point**, and a **fallback** cell that shows the same result from cache.
#
# | Section | When |
# |---|---|
# | A · Pre-flight checks | Morning of the session |
# | B · Prep 0a–0f | Days before (one-off) |
# | C · Act 1 live steps 1–4 | 0:05–0:25 |
# | D · Act 2 cues 5–9 | 0:25–0:55 |

# %% [markdown]
# ## Setup

# %%
import re
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# "synthetic": generate fake data with the real schemas (works today, no logins).
# "carto":     read the real cached tables through a CARTO login (route still TBC with CARTO).
DATA_MODE = "synthetic"

# Filled in once the tables exist. Placeholders only: this repo is public.
TABLES = {
    "market":    "<carto-dw-project>.<dataset>.carto_sdsc_h3_r9",
    "messy":     "<carto-dw-project>.<dataset>.portfolio_messy",
    "truth":     "<carto-dw-project>.<dataset>.portfolio_ground_truth",
    "matches":   "<carto-dw-project>.<dataset>.cache_address_matches",
    "risk":      "<carto-dw-project>.<dataset>.cache_climate_risk",
}
MAP_LINKS = {
    "Map A (agent, no semantic model)": "https://clausa.app.carto.com/map/<map-a-id>",
    "Map B (agent + semantic model)":   "https://clausa.app.carto.com/map/<map-b-id>",
}
QUADRANT_LABELS = {
    "HG-LR": "Pursue", "HG-HR": "Watchlist", "LG-LR": "Hold", "LG-HR": "Reassess",
}
QUADRANT_COLORS = {"HG-LR": "#2a9d8f", "HG-HR": "#e76f51", "LG-LR": "#8ab17d", "LG-HR": "#e9c46a"}


def quadrant(high_growth, high_risk):
    return [f"{'HG' if g else 'LG'}-{'HR' if r else 'LR'}" for g, r in zip(high_growth, high_risk)]

# %%
# --- Synthetic data generator (same columns as the real cached tables) ---------------------
# Addresses, CLIPs and H3 ids below are FICTITIOUS. Nothing here is Cotality data.

SUFFIX = {"Parkway": "Pkwy", "Road": "Rd", "Drive": "Dr", "Boulevard": "Blvd",
          "Lane": "Ln", "Court": "Ct", "Street": "St", "Way": "Way"}
STREETS = ["Three Oaks", "Corkscrew", "Estero", "Coconut", "Williams", "Sandy", "Broadway",
           "River Ranch", "Via Coconut", "Pelican Colony", "Koreshan", "Cypress View",
           "Ben Hill Griffin", "Alico", "Heron Bay", "Mockingbird", "Palm Grove", "Bella Terra"]
CITIES = {"Estero": ["33928", "33967"], "Bonita Springs": ["34134", "34135"],
          "Fort Myers": ["33908", "33912", "33913"]}
ERROR_MIX = {"clean": .40, "abbreviation": .15, "typo": .10, "missing_zip": .10,
             "case_whitespace": .10, "unit": .10, "wrong_city": .05}


def _market(rng, n_cells=450):
    lat = rng.uniform(26.36, 26.50, n_cells)
    lon = rng.uniform(-81.86, -81.72, n_cells)

    def bumps(centers):
        z = np.zeros(n_cells)
        for (cy, cx, s, w) in centers:
            z += w * np.exp(-(((lat - cy) / s) ** 2 + ((lon - cx) / s) ** 2))
        return z

    g = bumps([(26.44, -81.76, .03, 1.0), (26.38, -81.80, .025, .7), (26.48, -81.83, .02, .5)])
    growth = np.clip(0.005 + 0.12 * g + rng.normal(0, .006, n_cells), 0, None)
    coast = (lon + 81.72) / -0.14                     # 0 inland (east) -> 1 coast (west)
    crs = np.clip(20 + 60 * coast + 15 * bumps([(26.42, -81.78, .02, 1)]) +
                  rng.normal(0, 6, n_cells), 1, 100)
    m = pd.DataFrame({
        "h3": [f"8944{i:011x}" for i in range(n_cells)],
        "lat": lat, "lon": lon,
        "total_parcels": rng.integers(8, 90, n_cells),
        "k1_growth_share": growth.round(4),
        "k1_crs_median": crs.round(1),
    })
    m["growth_parcels"] = (m.total_parcels * m.k1_growth_share).round().astype(int)
    hot = m.k1_growth_share > m.k1_growth_share.quantile(.85)
    m["hotspot_class"] = np.where(hot, "Hot spot (95%)", "Not significant")
    return m


def _address(rng):
    street = rng.choice(STREETS)
    suffix = rng.choice(list(SUFFIX))
    city = rng.choice(list(CITIES))
    unit = f" Apt {rng.integers(1, 30)}{rng.choice(list('ABCD'))}" if rng.random() < .2 else ""
    return f"{rng.integers(100, 29999)} {street} {suffix}{unit}, {city}, FL {rng.choice(CITIES[city])}"


def _corrupt(addr, kind, rng):
    if kind == "abbreviation":
        for full, ab in SUFFIX.items():
            if f" {full}" in addr and full != ab:
                return addr.replace(f" {full}", f" {ab}", 1)
        return addr.replace(" Apt ", " #")
    if kind == "typo":
        num, rest = addr.split(" ", 1)
        i = rng.integers(1, 5)
        return f"{num} {rest[:i]}{rest[i + 1:]}"
    if kind == "missing_zip":
        return re.sub(r" \d{5}$", "", addr)
    if kind == "case_whitespace":
        return addr.lower().replace(" ", "  ", 2).replace(",", "") + "."
    if kind == "unit":
        return re.sub(r" Apt (\d+)([A-D])", r" \1 \2", addr) if " Apt " in addr else addr + " #"
    if kind == "wrong_city":
        city = next(c for c in CITIES if f", {c}," in addr)
        other = rng.choice([c for c in CITIES if c != city])
        return addr.replace(f", {city},", f", {other},")
    return addr


def make_synthetic(seed=7, per_quadrant=250, n_dupes=100, n_unmatchable=30):
    rng = np.random.default_rng(seed)
    market = _market(rng)
    g_cut, r_cut = market.k1_growth_share.median(), market.k1_crs_median.median()
    market["quadrant"] = quadrant(market.k1_growth_share >= g_cut, market.k1_crs_median >= r_cut)

    # Portfolio ground truth: 250 parcels per quadrant
    rows = []
    for q, cells in market.groupby("quadrant"):
        pick = cells.sample(per_quadrant, replace=True, random_state=int(rng.integers(10**6)))
        for _, c in pick.iterrows():
            rows.append({"clip": str(rng.integers(10**9, 10**10)), "address": _address(rng),
                         "lat": c.lat + rng.normal(0, .002), "lon": c.lon + rng.normal(0, .002),
                         "h3": c.h3})
    parcels = pd.DataFrame(rows).drop_duplicates("clip").reset_index(drop=True)

    # Messy CSV: one error type per base row + duplicates + unmatchable
    kinds = rng.choice(list(ERROR_MIX), size=len(parcels), p=list(ERROR_MIX.values()))
    truth = parcels.assign(error_type=kinds, is_duplicate=False)
    truth["raw_address"] = [_corrupt(a, k, rng) for a, k in zip(truth.address, truth.error_type)]
    dup = truth.sample(n_dupes, random_state=seed).copy()
    dup["error_type"] = rng.choice(["case_whitespace", "abbreviation", "clean"], n_dupes)
    dup["raw_address"] = [_corrupt(a, k, rng) for a, k in zip(dup.address, dup.error_type)]
    dup["is_duplicate"] = True
    junk = pd.DataFrame({
        "raw_address": [f"{rng.integers(1, 99)} Nowhere {rng.choice(['Loop', 'Trail'])}, FL"
                        for _ in range(n_unmatchable)],
        "error_type": "unmatchable", "is_duplicate": False})
    truth = (pd.concat([truth, dup, junk], ignore_index=True)
             .sample(frac=1, random_state=seed).reset_index(drop=True))
    truth.insert(0, "input_id", [f"P{i:05d}" for i in range(1, len(truth) + 1)])
    truth["book_value_usd"] = (rng.lognormal(12.9, .5, len(truth)) // 1000 * 1000).astype(int)

    messy = truth[["input_id", "raw_address", "book_value_usd"]].copy()
    ground_truth = truth[["input_id", "clip", "error_type", "is_duplicate"]].rename(
        columns={"clip": "true_clip"})

    # Cached Address Recognition -> clip results
    p_match = {"clean": .99, "case_whitespace": .98, "abbreviation": .97, "missing_zip": .93,
               "unit": .9, "typo": .86, "wrong_city": .82, "unmatchable": 0}
    matched = rng.random(len(truth)) < truth.error_type.map(p_match)
    conf = np.where(truth.error_type == "clean", rng.uniform(.95, 1, len(truth)),
                    rng.uniform(.7, .97, len(truth)))
    matches = pd.DataFrame({
        "input_id": truth.input_id,
        "match_status": np.where(matched, "matched", "no_match"),
        "match_confidence": np.where(matched, conf.round(2), np.nan),
        "clip": np.where(matched, truth["clip"], None),
        "resolved_address": np.where(matched, truth.address, None),
        "lat": np.where(matched, truth.lat, np.nan),
        "lon": np.where(matched, truth.lon, np.nan),
        "h3": np.where(matched, truth.h3, None),
    })
    wrong = matched & (truth.error_type == "typo") & (rng.random(len(truth)) < .05)
    matches.loc[wrong, "clip"] = [str(rng.integers(10**9, 10**10)) for _ in range(wrong.sum())]

    # Cached climate risk (CRS proxy until the Cotality climate MCP is available)
    risk = parcels[["clip", "h3"]].merge(market[["h3", "k1_crs_median"]], on="h3")
    risk["risk_score"] = np.clip(risk.k1_crs_median + rng.normal(0, 8, len(risk)), 1, 100).round()
    risk["risk_source"] = "crs_proxy"
    risk = risk[["clip", "risk_score", "risk_source"]]

    return {"market": market.drop(columns="quadrant"), "messy": messy, "truth": ground_truth,
            "matches": matches, "risk": risk}

# %%
def load_all():
    if DATA_MODE == "synthetic":
        return make_synthetic()
    # CARTO route (to confirm with CARTO): OAuth in the browser, then a BigQuery client
    # scoped to the CARTO Data Warehouse / our connection.
    import subprocess, sys
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "carto-auth"], check=True)
    from carto_auth import CartoAuth
    bq = CartoAuth.from_oauth().get_bigquery_client()
    return {k: bq.query(f"SELECT * FROM `{fqn}`").to_dataframe() for k, fqn in TABLES.items()}


data = load_all()
for name, df in data.items():
    print(f"{name:8s} {len(df):>6,} rows  {list(df.columns)}")

# %% [markdown]
# ## A · Pre-flight checks
#
# - [ ] VS Code: Copilot Chat in **agent mode**, Claude model selected
# - [ ] `.vscode/mcp.json` lists **cotality** and **carto**; both show as *running* in the MCP view
# - [ ] CARTO MCP signed in with **OAuth** (API tokens are read-only: no Workflow runs, no maps)
# - [ ] Workflow tool `score_portfolio_to_map` is **enabled** and synced to the latest version
# - [ ] Maps A and B open in an incognito window with a test temp account
# - [ ] Fallback recordings for steps 2, 3 and 4 open in a browser tab
#
# Expected `.vscode/mcp.json` shape (VS Code format; Cotality entry TBC):
#
# ```json
# {
#   "servers": {
#     "carto":    { "type": "http", "url": "https://<region>.api.carto.com/mcp/<account_id>" },
#     "cotality": { "type": "http", "url": "<cotality-mcp-url>" }
#   }
# }
# ```

# %%
import json
from pathlib import Path

cfg = Path("../.vscode/mcp.json")
if cfg.exists():
    servers = json.loads(cfg.read_text()).get("servers", {})
    for s in ("carto", "cotality"):
        print(f"{'OK ' if s in servers else 'MISSING'} {s}")
else:
    print("No .vscode/mcp.json yet (expected at repo root).")
print("DATA_MODE:", DATA_MODE)

# %% [markdown]
# ## B · Prep (one-off, days before)
#
# | # | Step | How | Done when |
# |---|---|---|---|
# | 0a | Market layer | Run `sql/h3_growth_crs.sql` in BigQuery | ≥ 250 portfolio candidates per quadrant (check below) |
# | 0b | Messy portfolio | Generator: 1,000 parcels → ~1,130 messy rows + ground truth | Error mix matches `docs/part1_data_workflow.md` |
# | 0c | Cache Cotality calls | Address Recognition → CLIP on every row; climate risk on every CLIP | Cache tables written; no rate-limit gaps |
# | 0d | `portfolio_scored` | Steps 1–4 below on the full cache | Quadrant counts look balanced |
# | 0e | Maps A and B | Builder: same layers; agent without YAML (A) / with YAML (B); share to workshop org | Opens with a temp account |
# | 0f | Workflow tool | Workflows: join → classify → Save as Table → Create Builder Map (Update) → MCP Tool Output; publish as `score_portfolio_to_map` | `Run MCP Tool test` returns map URL + counts |

# %%
# 0a: are there enough cells (and parcels) in each quadrant of the market layer?
mk = data["market"]
q = quadrant(mk.k1_growth_share >= mk.k1_growth_share.median(),
             mk.k1_crs_median >= mk.k1_crs_median.median())
display(mk.assign(quadrant=q).groupby("quadrant")
          .agg(cells=("h3", "size"), parcels=("total_parcels", "sum")))

# %%
# 0c: cache builder skeleton for the real Cotality calls (runs in prep, never on stage).
import time


def cache_calls(items, call, batch_size=50, pause_s=1.0):
    """Call `call(batch)` in batches, pausing between them; returns a list of results."""
    out = []
    for i in range(0, len(items), batch_size):
        out.extend(call(items[i:i + batch_size]))
        time.sleep(pause_s)
    return out


def resolve_addresses(batch):
    raise NotImplementedError("Cotality Address Recognition -> CLIP (endpoint and auth TBC)")


def climate_risk(batch):
    raise NotImplementedError("Cotality climate risk MCP/API (fields and limits TBC)")

# %% [markdown]
# ## C · Act 1 — live in Copilot Chat
#
# ### Step 1 · Profile (0:05–0:10)
#
# **Prompt**
# > Profile `data/portfolio_messy.csv`: how many rows, what kinds of address problems, how many
# > likely duplicates? Use the data-profiler skill and give me a short table.
#
# **Expect:** the agent reads the file, runs the profiler skill, returns a table of issue counts.
# **Say:** "Around 60% of a real address list needs cleanup before any join works."
# **Fallback:** run the cell below.

# %%
ABBREV = r"\b(?:" + "|".join(v for k, v in SUFFIX.items() if k != v) + r")\b"


def profile(df):
    a = df.raw_address
    flags = pd.DataFrame({
        "missing_zip": ~a.str.contains(r"\b\d{5}\.?$"),
        "formatting": a.str.contains("  ") | a.str.endswith(".") | (a == a.str.lower()),
        "abbreviated_suffix": a.str.contains(ABBREV, case=False),
        "odd_unit": a.str.contains(r"#|\b\d+ [A-D]\b"),
    })
    key = (a.str.lower().str.replace(r"[^a-z0-9 ]", "", regex=True)
            .str.replace(r"\s+", " ", regex=True).str.replace(r" \d{5}$", "", regex=True))
    flags["possible_duplicate"] = key.duplicated(keep=False)
    return flags


flags = profile(data["messy"])
summary = flags.sum().sort_values(ascending=False)
print(f"Rows: {len(flags):,}   rows with >=1 flag: {flags.any(axis=1).mean():.0%}")
summary.plot.barh(title="Rule-based profiler: rows flagged", figsize=(6, 2.5)); plt.show()

# %% [markdown]
# ### Step 2 · Resolve 5 addresses (0:10–0:15)
#
# The cell below picks 5 demo rows, one per interesting error type. Paste them into the prompt.

# %%
t = data["messy"].merge(data["truth"], on="input_id")
demo = pd.concat([t[t.error_type == k].head(1) for k in
                  ["clean", "abbreviation", "typo", "missing_zip", "wrong_city"]])
print("\n".join(f"{r.input_id}: {r.raw_address}" for r in demo.itertuples()))

# %% [markdown]
# **Prompt**
# > Resolve these 5 addresses with Cotality Address Recognition, then look up each CLIP.
# > Return a table: input id, raw address, resolved address, CLIP, confidence. *(paste the 5 rows)*
#
# **Expect:** 5 × Address Recognition calls, then 5 × CLIP lookups (Cotality MCP).
# **Say:** "The CLIP is the join key for everything downstream: risk, growth, the map."
# **Fallback:** the same rows from cache:

# %%
data["matches"][data["matches"].input_id.isin(demo.input_id)].merge(
    demo[["input_id", "raw_address", "error_type"]], on="input_id")

# %%
m = data["matches"].merge(data["truth"], on="input_id")
m["correct_clip"] = m["clip"] == m["true_clip"]

by_type = (m.groupby("error_type")
             .agg(rows=("input_id", "size"),
                  match_rate=("match_status", lambda s: (s == "matched").mean()),
                  correct_clip_rate=("correct_clip", "mean"))
             .sort_values("match_rate"))
display(by_type.style.format({"match_rate": "{:.0%}", "correct_clip_rate": "{:.0%}"}))

matched = m[m.match_status == "matched"]
portfolio = (matched.sort_values("match_confidence", ascending=False)
                    .drop_duplicates("clip")
                    .merge(data["messy"][["input_id", "raw_address", "book_value_usd"]], on="input_id"))
print(f"{len(m):,} input rows -> {len(matched):,} matched -> {len(portfolio):,} unique CLIPs "
      f"({len(matched) - len(portfolio)} duplicates collapsed, {(m.match_status != 'matched').sum()} unmatched)")

# %% [markdown]
# ### Step 3 · Climate risk for 1–2 CLIPs (0:15–0:20)
#
# **Prompt**
# > Get the climate risk for CLIPs *(paste 2 CLIPs from the table above)*. Summarise the perils and
# > the overall score in two lines.
#
# **Expect:** 2 × climate risk calls (Cotality MCP). Until climate MCP access exists, the cache uses
# the Parcel Essentials CRS proxy (`risk_source = crs_proxy`).
# **Say:** "Risk on demand at property level — including brand-new parcels a snapshot misses."
# **Fallback:** the cell below.

# %%
portfolio = portfolio.merge(data["risk"], on="clip", how="left")
print(f"Risk coverage: {portfolio.risk_score.notna().mean():.0%}  "
      f"(source: {', '.join(portfolio.risk_source.dropna().unique())})")
portfolio.risk_score.plot.hist(bins=20, title="Portfolio risk score (1-100)", figsize=(6, 2.5)); plt.show()

# %% [markdown]
# ### Step 4 · Publish to CARTO (0:20–0:25)
#
# **Prompt**
# > Publish the scored portfolio to CARTO with the `score_portfolio_to_map` tool,
# > `run_label = "live"`, default cutoffs. Give me the map link and the count per quadrant.
#
# **Expect:** 1 call to the published Workflow tool via CARTO MCP → writes
# `portfolio_scored_live`, updates the "SDSC live demo" map, returns URL + counts.
# **Optional follow-up:** "Republish with a risk cutoff of 70" (one more call, same tool).
# **Say:** "Same pattern as Cotality: governed logic packaged as a tool any agent can call."
# **If the tool fails:** "Do the same with built-in tools: write the table with SQL and create a map."
# **If CARTO MCP is down:** open the pre-built Map B.
#
# The cell below shows the counts the tool should return.

# %%
market = data["market"]
GROWTH_CUTOFF = market.k1_growth_share.median()   # study-area medians (try other values!)
RISK_CUTOFF = market.k1_crs_median.median()


def score(portfolio, growth_cutoff, risk_cutoff):
    p = portfolio.merge(market[["h3", "k1_growth_share", "hotspot_class"]], on="h3", how="left")
    p["quadrant"] = quadrant(p.k1_growth_share >= growth_cutoff, p.risk_score >= risk_cutoff)
    p["action_label"] = p.quadrant.map(QUADRANT_LABELS)
    return p


def plot_2x2(p, growth_cutoff, risk_cutoff):
    fig, ax = plt.subplots(figsize=(6.5, 5))
    for q, g in p.groupby("quadrant"):
        ax.scatter(g.k1_growth_share, g.risk_score, s=8, alpha=.6, c=QUADRANT_COLORS[q],
                   label=f"{QUADRANT_LABELS[q]} ({q}): {len(g)}")
    ax.axvline(growth_cutoff, c="k", lw=.8); ax.axhline(risk_cutoff, c="k", lw=.8)
    ax.set(xlabel="Neighbourhood growth share (H3 k-ring)", ylabel="Property risk score",
           title="Portfolio: growth x climate risk")
    ax.legend(loc="upper left", fontsize=8, bbox_to_anchor=(1, 1)); plt.show()


scored = score(portfolio, GROWTH_CUTOFF, RISK_CUTOFF)
plot_2x2(scored, GROWTH_CUTOFF, RISK_CUTOFF)
display(scored.groupby("action_label").agg(properties=("clip", "size"),
                                            book_value_usd=("book_value_usd", "sum")))

# %% [markdown]
# ## D · Act 2 cues (CARTO Builder)
#
# | # | Time | Cue |
# |---|---|---|
# | 5 | 0:25–0:30 | Open Map B: H3 bivariate layer (growth × risk), portfolio points, widgets. Click one Watchlist property. |
# | 6 | 0:30–0:40 | Show the YAML: grain (H3 res 9), metric (k-ring growth share), join key (h3), risk meaning. Hand out questions below; audience asks Map A then Map B. |
# | 7 | 0:40–0:44 | Hotspot layer: significant clusters, not raw counts. |
# | 8 | 0:44–0:50 | Cutoff sliders → Pursue / Watchlist / Hold / Reassess counts move. Open the Watchlist table. |
# | 9 | 0:50–0:55 | Seed: "Which Pursue properties sit in hotspot cells?" Then open questions. |
#
# **Prepared questions for Map A vs Map B (draft)**
#
# 1. "Where is growth highest?": without the YAML the agent may rank raw `growth_parcels`
#    (favours big cells); with it, it uses `k1_growth_share`.
# 2. "What's the average growth near my Watchlist properties?": needs the h3 join key and the
#    k-ring metric definition.
# 3. "How many of my properties are high risk?": needs the risk scale (1–100) and the cutoff rule.
