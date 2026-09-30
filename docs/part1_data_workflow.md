# Part 1 — Data workflow (working draft)

Status: study area locked; H3 layer query drafted (`sql/h3_growth_crs.sql`, not yet run). Everything
below the study area is an **assumption** to size outputs for CARTO; revisit once MCP access lands.

## 1. Study area (locked)

8 contiguous tracts in south Lee County, FL (Estero / San Carlos Park / Three Oaks). Started from
`scripts/select_tracts.py`, then revised after map review: dropped 050322, added 050502, 050601, 050603.

| GEOID | Tract quadrant | Parcels | Growth parcels | Tract CRS (mean) | High-CRS share |
|---|---|---|---|---|---|
| 12071050318 | HG-LR | 4,518 | 365 | 47.5 | 0.18 |
| 12071050401 | HG-HR | 2,931 | 286 | 62.2 | 0.38 |
| 12071050601 | HG-HR | 2,697 | 112 | 73.1 | 0.82 |
| 12071050317 | HG-HR | 2,300 | 66 | 70.0 | 0.60 |
| 12071050603 | HG-HR | 1,835 | 61 | 67.5 | 0.48 |
| 12071050316 | LG-LR | 2,776 | 2 | 48.9 | 0.01 |
| 12071050315 | LG-LR | 1,770 | 0 | 50.8 | 0.14 |
| 12071050502 | LG-LR | 2,656 | 28 | 44.5 | 0.04 |
| **Total** | | **21,483** | **920** | 46.8 km² (~450 res-9 cells) | |

Tract quadrants use Lee County medians (growth share 0.018, CRS 54). No tract is LG-HR, but the
workshop 2×2 is computed per H3 cell: the four high-risk tracts hold ~9.8K parcels with only ~5%
growth, so most of their cells should land in LG-HR. **Verify with the check query in
`sql/h3_growth_crs.sql`**; if LG-HR has < 250 parcels, add 050322 back.

## 2. Leadership data ask

| Layer | Grain | Rows (approx.) | Fields | Exposure |
|---|---|---|---|---|
| H3 growth × risk | H3 res 9 cell (cells < 5 parcels suppressed) | ~450 | parcel count, growth count/share, CRS median, k-ring smoothed values, quadrant | CARTO Marketplace |
| Portfolio sample | Parcel (CLIP) | 1,000 (250 per quadrant) | CLIP, address, lat/lon, H3, quadrant | CARTO Marketplace + workshop repo (as messy CSV) |
| Climate risk | Parcel (CLIP) | ~1,000 | MCP climate risk outputs (fields TBD) | Cached results only |

Nothing else leaves the internal work dataset (`carto_sdsc_parcels_h3` stays internal).

## 3. Pipeline and assumptions

Two datasets play different roles:

- **Market layer** — all ~21.5K parcels in the study area, aggregated to ~450 H3 cells. Answers
  *"where is growth, where is risk?"*. Drives the growth axis, the hotspots and the map background.
  Nobody handles these parcels individually.
- **Portfolio** — 1,000 parcels sampled from the same area, turned into ~1,130 messy address rows.
  Answers *"which of **my** properties sit where?"*. This is what gets cleaned, enriched and put on the 2×2.

```
21.5K parcels ──► H3 market layer (~450 cells) ──────────────┐
      │                                                      ├──► 2×2 per portfolio property ──► CARTO
      └─ sample 1K ──► messy CSV (~1,130) ──► CLIP ──► risk ─┘
```

Steps 0a–0b are pre-work (audience never sees them run). Steps 1–6 are the live workshop.

| # | Step | Dataset (rows in → out) | What we do (pre-work) | What the audience does | Takeaway | Complexity | Needed to unlock |
|---|---|---|---|---|---|---|---|
| 0a | Build market layer | Market: 21.5K parcels → ~450 H3 cells | Run `sql/h3_growth_crs.sql`: res 9 cells, k-ring smoothing, suppress cells < 5 parcels; publish to CARTO. | Nothing (sees it as the opening map). | — | **Low**: SQL drafted. | Leadership approval to expose H3 aggregates; US-region BQ dataset for `carto-os`. |
| 0b | Build messy portfolio | Portfolio: 1,000 parcels → ~1,130 messy rows | Sample 250 per quadrant; attach situs addresses; corrupt ~60% with a known error type (mix below); add ~100 duplicates, ~30 unmatchable; keep ground truth hidden. | Nothing (receives the CSV). | — | **Medium**: needs a generator script. | Address source table; approval to publish ~1K addresses in a public repo. |
| 1 | Profile messy addresses | Portfolio: ~1,130 rows | Adapt the open-source data-profiler skill; Python fallback. | Loads CSV in Colab, runs the profiler, sees the error breakdown. | "~60% of a real-world address list needs cleanup before any join works." | **Low–Medium**. | Skill tweak; Colab notebook. |
| 2 | Resolve to CLIP + dedupe | Portfolio: ~1,130 → ~1,000 unique CLIPs | Pre-run Address Recognition API → clip tool on all rows (~2,260 calls), cache results. | Watches ~5 live calls; loads cached results; compares match rate by error type vs ground truth; sees duplicates collapse. | "A persistent property ID (CLIP) is the join key for everything downstream." | **High**: two external APIs, quotas, batching. | API + MCP access and credentials; batch endpoint / rate limits. |
| 3 | Enrich climate risk | Portfolio: ~1,000 CLIPs | Pre-run climate risk MCP (~1,000 calls), cache. Until access: CRS proxy (cell median) with the same schema. | Watches 1–2 live calls; loads cached results. | "Risk on demand at property level, including new parcels that snapshot data misses (~95% gap)." | **High**: access pending; output fields unknown. | Climate MCP access; field list and perils; rate limits. |
| 4 | Add growth via semantic model | Market layer joined to portfolio (CLIP → H3) | Write the YAML (grain = H3 res 9, metric = k-ring growth share, join key, suppression rule); publish the market layer to the Marketplace. | Asks the agent a growth question **without** then **with** the YAML; sees the wrong vs right answer; joins growth to the portfolio. | "The semantic model is what makes agent answers correct and repeatable." | **Medium–High**: designing a convincing before/after. | Growth schema + old YAML; agent runtime (presenter-side). |
| 5 | Growth hotspots | Market: ~450 cells | Run `GETIS_ORD_H3` on `k1_growth_share`; pre-compute as backup. | Runs one Analytics Toolbox call in CARTO, or views the result. | "Statistically significant growth clusters, not just high counts." | **Medium**. | CARTO accounts with a BQ connection and Analytics Toolbox for attendees. |
| 6 | Growth × risk 2×2 | Portfolio: ~1,000 → `portfolio_scored` | Fix cutoffs (study-area medians) and labels: HG-LR **Pursue**, HG-HR **Watchlist**, LG-LR **Hold**, LG-HR **Reassess**. | Runs the quadrant cell; sees counts per quadrant and the watchlist. | "Where new activity concentrates in high-hazard zones." | **Low**. | Outputs of steps 2–5. |

Messy-address error mix for step 0b (share of 1,000 base addresses, one type per row):

| Error type | Example | Share |
|---|---|---|
| Clean | `21000 Three Oaks Pkwy, Estero, FL 33928` | 40% |
| Abbreviation / suffix | `Pkwy` ↔ `Parkway`, `N` ↔ `North` | 15% |
| Typo | `Thre Oaks` | 10% |
| Missing / wrong ZIP | `…, Estero, FL` | 10% |
| Case / whitespace / punctuation | `21000  three oaks pkwy.` | 10% |
| Missing or malformed unit | `Apt 4` dropped, `#4B` → `4 B` | 10% |
| Wrong city (valid ZIP) | `Fort Myers` for an Estero ZIP | 5% |

## 4. Outputs for CARTO (Part 2 input)

**`carto_sdsc_h3_r9`** (polygon layer): `h3`, `total_parcels`, `growth_parcels`, `growth_share`,
`k1_growth_share`, `crs_median`, `k1_crs_median`, `quadrant`, plus the Getis-Ord hotspot z-score and p-value
computed in CARTO (`GETIS_ORD_H3` on `k1_growth_share`).

**`portfolio_scored`** (point layer, ~1,000 rows after dedupe):

| Field | Source |
|---|---|
| `input_id`, `raw_address`, `error_type` | Step 0b (ground truth) |
| `resolved_address`, `match_status`, `match_confidence`, `clip` | Step 2 |
| `lat`, `lon`, `h3` | Step 2 / market layer |
| `k1_growth_share`, `hotspot_class` | Steps 4–5 |
| `risk_score`, `risk_source` (`crs_proxy` \| `climate_mcp`) | Step 3 |
| `quadrant`, `action_label` | Step 6 |

**Insights the data should support**
1. Address quality: X% of rows are messy, Y duplicates collapse to Z unique CLIPs, match rate by error type.
2. Portfolio split across Pursue / Watchlist / Hold / Reassess.
3. Growth hotspots (Getis-Ord) that overlap high-risk cells: the watchlist.
4. Data freshness: ~95% of new-growth parcels have no Parcel Essentials risk score, so an on-demand MCP call beats a snapshot.

## 5. Part 3 constraints to carry forward

- Attendees have no Cotality MCP credentials or LLM key: live calls run on the presenter's machine; attendees get cached results with identical schemas.
- CARTO Analytics Toolbox (`carto-os`) functions require a US multi-region BigQuery dataset; `GETIS_ORD_H3` needs a CARTO account/connection for attendees.
- Only the two exposable layers in §2 may reach the Marketplace or the (public) repo.
- 60 min session: every notebook cell must finish in seconds; keep pre-recorded fallbacks for each live step.

## 6. Open questions

- Which table has situs addresses for the portfolio sample (needed for step 3)?
- Climate risk MCP: output fields, perils, rate limits.
- Address Recognition API: batch endpoint and quota for ~1,100 rows.
