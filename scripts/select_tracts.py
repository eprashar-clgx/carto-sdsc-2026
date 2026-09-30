"""Select the smallest contiguous Lee County tract cluster covering all four growth x risk quadrants.

Input : data/carto_sdsc_growth_tracts_w_crs_v2.csv (output of sql/growth_crs_tracts.sql)
Output: data/selected_tracts.csv

Quadrants use county percentiles of tract growth_share and crs_mean. Tract-level CRS stands in
for location risk (growth-parcel CRS coverage is only ~5%). A quadrant's "count" is growth
parcels for high-growth quadrants and total parcels for low-growth quadrants.
"""
import collections
import math
import re

import pandas as pd

SRC = "data/carto_sdsc_growth_tracts_w_crs_v2.csv"
OUT = "data/selected_tracts.csv"
FIPS = "12071"
MIN_TRACT_PARCELS = 300
MIN_CRS_COVERAGE = 0.5
TARGET_PER_QUADRANT = 200
MAX_TRACTS = 25
QUADRANTS = ["HG-HR", "HG-LR", "LG-HR", "LG-LR"]


def km(a, b) -> float:
    return math.hypot((a[1] - b[1]) * 111.32 * math.cos(math.radians(a[0])), (a[0] - b[0]) * 110.57)


def area_km2(wkt: str) -> float:
    total = 0.0
    for ring in re.findall(r"\(\(([^()]+)\)", wkt):
        pts = [tuple(map(float, p.split())) for p in ring.split(",")]
        lat0 = math.radians(sum(y for _, y in pts) / len(pts))
        xy = [(x * 111.32 * math.cos(lat0), y * 110.57) for x, y in pts]
        total += abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))) / 2
    return total


def neighbours(geoms: pd.Series) -> dict:
    """Tracts sharing >= 2 boundary vertices (TIGER tracts share exact vertices)."""
    owners = collections.defaultdict(set)
    for g, wkt in geoms.items():
        nums = re.findall(r"-?\d+\.\d+", wkt)
        for v in zip(nums[0::2], nums[1::2]):
            owners[v].add(g)
    shared = collections.defaultdict(collections.Counter)
    for gs in owners.values():
        for a in gs:
            for b in gs:
                if a != b:
                    shared[a][b] += 1
    return {g: {b for b, c in shared[g].items() if c >= 2} for g in geoms.index}


def main() -> None:
    df = pd.read_csv(SRC, dtype={"fips": str, "TRACTCE": str, "geoid": str})
    x = df[(df.fips == FIPS) & df.geometry.notna() & (df.total_parcels >= MIN_TRACT_PARCELS)
           & (df.crs_coverage >= MIN_CRS_COVERAGE)].copy().set_index("geoid")
    x["growth_pct"] = x.growth_share.rank(pct=True)
    x["risk_pct"] = x.crs_mean.rank(pct=True)
    x["quadrant"] = [("HG" if g >= 0.5 else "LG") + "-" + ("HR" if r >= 0.5 else "LR")
                     for g, r in zip(x.growth_pct, x.risk_pct)]
    x["contrib"] = [gp if q.startswith("HG") else tp
                    for q, gp, tp in zip(x.quadrant, x.growth_parcels, x.total_parcels)]

    nbr = neighbours(x.geometry)
    ctr = {g: (r.ctr_lat, r.ctr_lon) for g, r in x.iterrows()}

    # Grow a contiguous region from each seed, adding the nearest frontier tract until all quadrants hit target
    solutions = []
    for seed in x.index:
        region, frontier = [seed], set(nbr[seed])
        tot = collections.Counter({x.at[seed, "quadrant"]: x.at[seed, "contrib"]})
        while not all(tot[q] >= TARGET_PER_QUADRANT for q in QUADRANTS) and frontier and len(region) < MAX_TRACTS:
            nxt = min(frontier, key=lambda g: km(ctr[g], ctr[seed]))
            frontier.discard(nxt)
            region.append(nxt)
            tot[x.at[nxt, "quadrant"]] += x.at[nxt, "contrib"]
            frontier |= {y for y in nbr[nxt] if y not in region}
        if all(tot[q] >= TARGET_PER_QUADRANT for q in QUADRANTS):
            solutions.append((len(region), int(x.loc[region, "total_parcels"].sum()), region))

    n, total, region = min(solutions, key=lambda s: (s[0], s[1]))
    sel = x.loc[region].reset_index()
    sel["area_km2"] = sel.geometry.map(area_km2).round(2)
    sel.to_csv(OUT, index=False)

    cols = ["quadrant", "geoid", "total_parcels", "growth_parcels", "growth_share", "crs_mean",
            "crs_coverage", "growth_pct", "risk_pct", "area_km2"]
    print(sel[cols].sort_values("quadrant").round(3).to_string(index=False))
    print(f"\n{len(solutions)} contiguous solutions; chose {n} tracts, {total} parcels, "
          f"{sel.area_km2.sum():.1f} km2, {total / sel.area_km2.sum():.0f} parcels/km2")
    print("per-quadrant count:", sel.groupby("quadrant").contrib.sum().to_dict())
    print("GEOIDs:", ", ".join(f"'{g}'" for g in sel.geoid))


if __name__ == "__main__":
    main()
