-- H3 (res 9) growth x risk layers for the locked study area: 8 contiguous tracts in south Lee County, FL.
-- Tract list: scripts/select_tracts.py output, revised manually after map review (dropped 050322,
-- added 050502, 050601, 050603).
-- Outputs:
--   1. carto_sdsc_parcels_h3    internal only; parcel-level inputs (NOT for Marketplace)
--   2. carto_sdsc_h3_r9         exposable H3 aggregates (growth share, CRS) + quadrant per cell
--   3. carto_sdsc_portfolio     exposable parcel-level sample, ~250 per quadrant (feeds messy-address step)
-- H3 functions are from the CARTO Analytics Toolbox (carto-os, US multi-region).
-- CRS = Parcel Essentials composite risk score, a proxy until the Cotality climate risk MCP is available.

DECLARE study_tracts ARRAY<STRING> DEFAULT
  ['12071050318','12071050401','12071050317','12071050315','12071050316',
   '12071050601','12071050603','12071050502'];
DECLARE h3_res INT64 DEFAULT 9;
DECLARE min_cell_parcels INT64 DEFAULT 5;      -- cells below this are suppressed in the exposed layer
DECLARE sample_per_quadrant INT64 DEFAULT 250;

-- 1) Parcel-level inputs (internal)
CREATE OR REPLACE TABLE `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_parcels_h3` AS
WITH growth AS (
  SELECT DISTINCT CAST(puid AS STRING) AS clip
  FROM `clgx-idap-bigquery-prd-a990.edr_ent_property_enriched.vw_edr_panoramiq_growth_indicators_v2`
),
parcel_rows AS (
  SELECT * FROM (
    SELECT
      b.clip,
      b.parcel_shape_id,
      -- census_tract_id may be an 11-digit GEOID or a 6-digit TRACTCE
      IF(LENGTH(b.census_tract_id) = 6,
         CONCAT(b.state_code, b.cnty_code, b.census_tract_id),
         b.census_tract_id)        AS geoid,
      ST_CENTROID(b.geometry)      AS pt
    FROM `clgx-gis-app-prd-364d.boundary.basicpublicrecord_parcel_addon` b
    WHERE CONCAT(b.state_code, b.cnty_code) = '12071'
      AND b.clip IS NOT NULL
  )
  WHERE geoid IN UNNEST(study_tracts)
),
crs AS (
  SELECT parcel_shape_id, crs
  FROM `clgx-idap-bigquery-prd-a990.edr_ent_property_fulfillment_hist.parcel_essentials_dsf`
  WHERE parcel_shape_id IN (SELECT parcel_shape_id FROM parcel_rows)
)
SELECT
  p.clip,
  ANY_VALUE(p.geoid)                                          AS geoid,
  ST_CENTROID(ST_UNION_AGG(p.pt))                             AS pt,
  LOGICAL_OR(g.clip IS NOT NULL)                              AS is_growth,
  MAX(c.crs)                                                  AS crs,
  `carto-os.carto.H3_FROMGEOGPOINT`(ST_CENTROID(ST_UNION_AGG(p.pt)), h3_res) AS h3
FROM parcel_rows p
LEFT JOIN growth g ON g.clip = p.clip
LEFT JOIN crs c USING (parcel_shape_id)
GROUP BY p.clip;

-- 2) H3 aggregates. Growth and risk are smoothed over each cell + its 6 neighbours (k-ring 1)
--    because growth parcels are sparse (~2 per cell) and growth parcels rarely carry a CRS.
CREATE OR REPLACE TABLE `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_h3_r9` AS
WITH cells AS (
  SELECT
    h3,
    COUNT(*)                   AS total_parcels,
    COUNTIF(is_growth)         AS growth_parcels,
    COUNTIF(crs IS NOT NULL)   AS crs_parcels,
    APPROX_QUANTILES(crs, 2)[OFFSET(1)] AS crs_median
  FROM `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_parcels_h3`
  GROUP BY h3
),
ring AS (
  SELECT c.h3, n AS ring_h3
  FROM cells c, UNNEST(`carto-os.carto.H3_KRING`(c.h3, 1)) AS n
),
smoothed AS (
  SELECT
    r.h3,
    SUM(c.total_parcels)                                 AS k1_total_parcels,
    SUM(c.growth_parcels)                                AS k1_growth_parcels,
    SAFE_DIVIDE(SUM(c.growth_parcels), SUM(c.total_parcels)) AS k1_growth_share
  FROM ring r
  JOIN cells c ON c.h3 = r.ring_h3
  GROUP BY r.h3
),
smoothed_crs AS (
  SELECT r.h3, APPROX_QUANTILES(p.crs, 2)[OFFSET(1)] AS k1_crs_median
  FROM ring r
  JOIN `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_parcels_h3` p ON p.h3 = r.ring_h3
  WHERE p.crs IS NOT NULL
  GROUP BY r.h3
),
joined AS (
  SELECT
    c.h3,
    c.total_parcels,
    c.growth_parcels,
    ROUND(SAFE_DIVIDE(c.growth_parcels, c.total_parcels), 3) AS growth_share,
    c.crs_parcels,
    c.crs_median,
    s.k1_total_parcels,
    s.k1_growth_parcels,
    ROUND(s.k1_growth_share, 3)                               AS k1_growth_share,
    sc.k1_crs_median
  FROM cells c
  JOIN smoothed s USING (h3)
  LEFT JOIN smoothed_crs sc USING (h3)
  WHERE c.total_parcels >= min_cell_parcels
),
cutoffs AS (
  -- Study-area medians define the 2x2 split
  SELECT
    APPROX_QUANTILES(k1_growth_share, 2)[OFFSET(1)] AS growth_cut,
    APPROX_QUANTILES(k1_crs_median, 2)[OFFSET(1)]   AS risk_cut
  FROM joined
)
SELECT
  j.*,
  x.growth_cut,
  x.risk_cut,
  IF(j.k1_crs_median IS NULL, NULL,
     CONCAT(IF(j.k1_growth_share > x.growth_cut, 'HG', 'LG'), '-',
            IF(j.k1_crs_median  >= x.risk_cut,  'HR', 'LR'))) AS quadrant,
  `carto-os.carto.H3_BOUNDARY`(j.h3)                           AS geom
FROM joined j CROSS JOIN cutoffs x;

-- 3) Portfolio sample: deterministic, stratified by quadrant. Growth parcels are excluded because
--    a portfolio holds existing assets and new parcels often lack a situs address yet.
CREATE OR REPLACE TABLE `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_portfolio` AS
SELECT
  p.clip,
  p.geoid,
  p.h3,
  p.crs                 AS parcel_crs,       -- sparse; the 2x2 uses the cell-level k1 values
  h.k1_growth_share,
  h.k1_crs_median,
  h.quadrant,
  ST_X(p.pt)            AS lon,
  ST_Y(p.pt)            AS lat
  -- TODO: join situs address (street, unit, city, state, zip) from the address source table
FROM `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_parcels_h3` p
JOIN `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_h3_r9` h USING (h3)
WHERE NOT p.is_growth
  AND h.quadrant IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY h.quadrant ORDER BY FARM_FINGERPRINT(p.clip)) <= sample_per_quadrant;

-- Checks: quadrant balance at the H3 level and in the sample
WITH q AS (
  SELECT quadrant, COUNT(*) AS cells, SUM(total_parcels) AS parcels, SUM(growth_parcels) AS growth_parcels
  FROM `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_h3_r9`
  GROUP BY quadrant
),
s AS (
  SELECT quadrant, COUNT(*) AS sampled
  FROM `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_portfolio`
  GROUP BY quadrant
)
SELECT q.*, IFNULL(s.sampled, 0) AS sampled
FROM q LEFT JOIN s USING (quadrant)
ORDER BY quadrant;
