# %% [markdown]
# # SDSC 2026 — Portfolio screening: growth × climate risk (Act 1)
#
# You manage a portfolio of ~1,000 properties in Lee County, FL. The address list is messy. By the end
# of this notebook you will have:
#
# 1. **Profiled** the messy list — how bad is it?
# 2. **Resolved** every row to a Cotality property ID (CLIP) and removed duplicates
# 3. **Enriched** each property with a climate risk score
# 4. **Placed** each property on a growth × risk 2×2 — and then opened it in CARTO
#
# The presenter runs each step live with an AI agent on a few rows. You run the same step here on the
# **full, pre-computed (cached) results** — same data, same columns.
#
# > Run cells top to bottom with **Shift + Enter**. Every cell finishes in a few seconds.

# %% [markdown]
# ## 0 · Setup

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

# %% [markdown]
# <details><summary>Synthetic data generator (click to expand — only used while <code>DATA_MODE = "synthetic"</code>)</summary>
#
# Produces tables with exactly the columns of the real cached tables so the notebook runs end to end.
# </details>

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
# ## 1 · Profile the messy address list
#
# **Presenter:** asks the agent to profile the CSV.
# **You:** run simple rules over all rows. What share of rows would break a naive join?

# %%
data["messy"].sample(8, random_state=1)

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
# Now compare with the **ground truth** (we corrupted the list on purpose, so we know each row's real
# problem). Which problems did the rules miss?

# %%
check = flags.any(axis=1).groupby(data["truth"].error_type).mean().rename("flagged_by_rules")
display(check.to_frame().style.format("{:.0%}"))

# %% [markdown]
# > **Takeaway:** rules catch formatting, but not typos or wrong cities. Most of a real address list
# > needs a proper address-recognition service before any join works.
#
# ## 2 · Resolve addresses to CLIPs and remove duplicates
#
# **Presenter:** the agent calls Cotality **Address Recognition → CLIP** on 5 rows, live.
# **You:** load the cached results for *all* rows and score them against the ground truth.

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
# > **Takeaway:** the CLIP is a persistent property ID. Once every row has one, duplicates collapse and
# > every other dataset (risk, growth) joins on it.
#
# ## 3 · Add climate risk
#
# **Presenter:** the agent asks Cotality for the climate risk of 1–2 CLIPs, live.
# **You:** join the cached risk scores to your portfolio.

# %%
portfolio = portfolio.merge(data["risk"], on="clip", how="left")
print(f"Risk coverage: {portfolio.risk_score.notna().mean():.0%}  "
      f"(source: {', '.join(portfolio.risk_source.dropna().unique())})")
portfolio.risk_score.plot.hist(bins=20, title="Portfolio risk score (1-100)", figsize=(6, 2.5)); plt.show()

# %% [markdown]
# ## 4 · Growth × risk 2×2
#
# Growth comes from the **market layer**: every parcel in the study area rolled up to H3 cells.
# Each property takes the growth share of its neighbourhood (its cell plus the ring around it).
#
# | | Low risk | High risk |
# |---|---|---|
# | **High growth** | **Pursue** | **Watchlist** |
# | **Low growth** | **Hold** | **Reassess** |

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
# ### Try it: move the cutoffs
#
# Change the numbers and re-run. How many properties move into the **Watchlist**?

# %%
my_growth_cutoff, my_risk_cutoff = GROWTH_CUTOFF, 70
mine = score(portfolio, my_growth_cutoff, my_risk_cutoff)
plot_2x2(mine, my_growth_cutoff, my_risk_cutoff)

# %% [markdown]
# ### Your top Watchlist properties

# %%
(scored[scored.action_label == "Watchlist"]
    .sort_values(["risk_score", "k1_growth_share"], ascending=False)
    [["input_id", "resolved_address", "risk_score", "k1_growth_share", "hotspot_class", "book_value_usd"]]
    .head(10))

# %% [markdown]
# ## → Now open CARTO (Act 2)
#
# The same scored portfolio is already on shared maps. Sign in with your **CARTO workshop account**.
#
# - **Map A**: AI agent *without* the semantic model
# - **Map B**: AI agent *with* the semantic model (growth YAML)
#
# Ask both the same question and compare the answers.

# %%
from IPython.display import Markdown
Markdown("\n".join(f"- [{name}]({url})" for name, url in MAP_LINKS.items()))
