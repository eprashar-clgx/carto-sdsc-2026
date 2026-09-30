-- Tract-level growth share x composite risk score (CRS, 1-100) from Parcel Essentials.
-- CRS is a stand-in for the risk axis until Cotality climate risk MCP access is available.
-- Run profile_flood_tract_fields.sql (queries 1 and 3) first to confirm tract ID format and CRS snapshots.
DECLARE target_fips ARRAY<STRING> DEFAULT ['12071','48201','06065'];  -- Lee, Harris, Riverside
DECLARE min_parcels INT64 DEFAULT 1000;
DECLARE high_crs_min INT64 DEFAULT 70;  -- CRS at/above this counts as high risk; revisit after seeing the distribution

CREATE OR REPLACE TABLE `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_growth_crs_ct` AS
WITH growth AS (
  SELECT DISTINCT CAST(puid AS STRING) AS clip
  FROM `clgx-idap-bigquery-prd-a990.edr_ent_property_enriched.vw_edr_panoramiq_growth_indicators_v2`
),
parcel_rows AS (
  SELECT
    b.clip,
    b.parcel_shape_id,
    CONCAT(b.state_code, b.cnty_code) AS fips,
    b.census_tract_id,
    g.clip IS NOT NULL AS is_growth
  FROM `clgx-gis-app-prd-364d.boundary.basicpublicrecord_parcel_addon` b
  LEFT JOIN growth g ON g.clip = b.clip
  WHERE CONCAT(b.state_code, b.cnty_code) IN UNNEST(target_fips)
    AND b.clip IS NOT NULL
),
crs AS (
  SELECT parcel_shape_id, crs
  FROM `clgx-idap-bigquery-prd-a990.edr_ent_property_fulfillment_hist.parcel_essentials_dsf`
  WHERE parcel_shape_id IN (SELECT parcel_shape_id FROM parcel_rows)
  -- History table: if the profile shows >1 row per shape, keep the latest snapshot, e.g.
  -- QUALIFY ROW_NUMBER() OVER (PARTITION BY parcel_shape_id ORDER BY <snapshot_date_col> DESC) = 1
),
-- One row per CLIP; a CLIP spanning several shapes takes the highest (most conservative) CRS
parcels AS (
  SELECT
    p.clip,
    ANY_VALUE(p.fips)            AS fips,
    ANY_VALUE(p.census_tract_id) AS census_tract_id,
    LOGICAL_OR(p.is_growth)      AS is_growth,
    MAX(c.crs)                   AS crs
  FROM parcel_rows p
  LEFT JOIN crs c USING (parcel_shape_id)
  GROUP BY p.clip
),
tract_counts AS (
  SELECT
    fips,
    -- Assumes 11-digit GEOID or 6-digit TRACTCE; other formats yield NULL (see sanity check)
    CASE LENGTH(census_tract_id)
      WHEN 11 THEN census_tract_id
      WHEN 6  THEN CONCAT(fips, census_tract_id)
    END                                                   AS geoid,
    COUNT(*)                                              AS total_parcels,
    COUNTIF(is_growth)                                    AS growth_parcels,
    COUNTIF(crs IS NOT NULL)                              AS crs_parcels,
    ROUND(AVG(crs), 1)                                    AS crs_mean,
    APPROX_QUANTILES(crs, 100)[OFFSET(50)]                AS crs_median,
    APPROX_QUANTILES(crs, 100)[OFFSET(90)]                AS crs_p90,
    COUNTIF(crs >= high_crs_min)                          AS high_crs_parcels,
    COUNTIF(is_growth AND crs IS NOT NULL)                AS growth_crs_parcels,
    ROUND(AVG(IF(is_growth, crs, NULL)), 1)               AS growth_crs_mean,
    COUNTIF(is_growth AND crs >= high_crs_min)            AS growth_high_crs_parcels
  FROM parcels
  GROUP BY fips, geoid
),
tracts AS (
  SELECT GEOID AS geoid, TRACTCE, geometry
  FROM `clgx-gis-app-dev-06e3.boundary.census_tract`
  WHERE CONCAT(STATEFP, COUNTYFP) IN UNNEST(target_fips)
)
SELECT
  tc.fips,
  t.TRACTCE,
  tc.geoid,
  tc.total_parcels,
  tc.growth_parcels,
  tc.crs_parcels,
  tc.crs_mean,
  tc.crs_median,
  tc.crs_p90,
  tc.high_crs_parcels,
  tc.growth_crs_parcels,
  tc.growth_crs_mean,
  tc.growth_high_crs_parcels,
  ROUND(SAFE_DIVIDE(tc.growth_parcels, tc.total_parcels), 3)               AS growth_share,
  ROUND(SAFE_DIVIDE(tc.crs_parcels, tc.total_parcels), 3)                  AS crs_coverage,
  ROUND(SAFE_DIVIDE(tc.growth_crs_parcels, tc.growth_parcels), 3)          AS growth_crs_coverage,
  ROUND(SAFE_DIVIDE(tc.high_crs_parcels, tc.crs_parcels), 3)               AS high_crs_share,
  -- "Watchlist" signal: share of new growth landing on high-risk parcels
  ROUND(SAFE_DIVIDE(tc.growth_high_crs_parcels, tc.growth_crs_parcels), 3) AS growth_in_high_crs_share,
  tc.total_parcels >= min_parcels                                          AS meets_min_parcels,
  ST_Y(ST_CENTROID(t.geometry))                                            AS ctr_lat,
  ST_X(ST_CENTROID(t.geometry))                                            AS ctr_lon,
  t.geometry
FROM tract_counts tc
LEFT JOIN tracts t USING (geoid)
ORDER BY tc.fips, growth_share DESC;

-- Sanity checks
SELECT
  fips,
  COUNTIF(geoid IS NULL)                                  AS null_geoid_tracts,
  COUNTIF(geometry IS NULL)                               AS unmatched_geometry_tracts,
  ROUND(SAFE_DIVIDE(SUM(crs_parcels), SUM(total_parcels)), 3) AS county_crs_coverage,
  ROUND(SAFE_DIVIDE(SUM(growth_crs_parcels), SUM(growth_parcels)), 3) AS county_growth_crs_coverage,
  MIN(crs_mean)                                           AS min_tract_crs_mean,
  MAX(crs_mean)                                           AS max_tract_crs_mean
FROM `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_growth_crs_ct`
GROUP BY fips;
