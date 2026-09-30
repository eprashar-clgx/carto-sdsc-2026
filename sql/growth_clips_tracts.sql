-- declare fips
DECLARE target_fips ARRAY<STRING> DEFAULT ['12071','48201','06065'];  -- Lee, Harris, Riverside
DECLARE min_parcels INT64 DEFAULT 1000;


CREATE OR REPLACE TABLE `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_growth_ct` AS 
-- growth clips
WITH growth AS (
  SELECT DISTINCT CAST(puid AS STRING) AS clip
  FROM `clgx-idap-bigquery-prd-a990.edr_ent_property_enriched.vw_edr_panoramiq_growth_indicators_v2`
),
-- parcel centroid with geo ids
parcels AS (
  SELECT
    b.clip,
    CONCAT(b.state_code, b.cnty_code) AS fips,
    ST_CENTROID(b.geometry) AS pt,   -- or ST_CENTROID(b.<geom_col>)
    g.clip IS NOT NULL AS is_growth
  FROM `clgx-gis-app-prd-364d.boundary.basicpublicrecord_parcel_addon` b
  LEFT JOIN growth g ON g.clip = b.clip
  WHERE CONCAT(b.state_code, b.cnty_code) IN UNNEST(target_fips)
),
-- get census tracts with geometries
tracts AS (
  SELECT
    GEOID AS geoid,   -- full 11-digit tract ID
    CONCAT(STATEFP, COUNTYFP)          AS fips,
    TRACTCE,
    geometry
  FROM `clgx-gis-app-dev-06e3.boundary.census_tract`
  WHERE CONCAT(STATEFP, COUNTYFP) IN UNNEST(target_fips)
),
-- total clips and growth clips per tract
tract_counts AS (
  SELECT
    t.geoid,
    COUNT(DISTINCT p.clip)                        AS total_parcels,
    COUNT(DISTINCT IF(p.is_growth, p.clip, NULL)) AS growth_parcels
  FROM parcels p
  JOIN tracts t
    ON t.fips = p.fips
   AND ST_INTERSECTS(t.geometry, p.pt)
  GROUP BY t.geoid
)
-- tract-level with geo, count of clips, count of growth clips,
SELECT
  t.fips,
  t.TRACTCE,
  t.geoid,
  tc.total_parcels,
  tc.growth_parcels,
  ROUND(SAFE_DIVIDE(tc.growth_parcels, tc.total_parcels), 3) AS growth_share,
  tc.total_parcels >= min_parcels                           AS meets_min_parcels,
  RANK() OVER (PARTITION BY t.fips
               ORDER BY SAFE_DIVIDE(tc.growth_parcels, tc.total_parcels) DESC) AS share_rank_in_county,
  ST_Y(ST_CENTROID(t.geometry)) AS ctr_lat,
  ST_X(ST_CENTROID(t.geometry)) AS ctr_lon,
  t.geometry
FROM tract_counts tc
JOIN tracts t USING (geoid)            -- geometry joined on the full GEOID, not TRACTCE
ORDER BY t.fips, growth_share DESC;