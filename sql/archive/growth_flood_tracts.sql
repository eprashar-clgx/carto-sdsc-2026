-- Tract-level growth share x FEMA flood exposure, used to select workshop tracts.
-- FEMA is a selection proxy only; the workshop risk axis comes from Cotality climate MCP.
-- Confirm formats with profile_flood_tract_fields.sql first.
DECLARE target_fips ARRAY<STRING> DEFAULT ['12071','48201','06065'];  -- Lee, Harris, Riverside
DECLARE min_parcels INT64 DEFAULT 1000;
DECLARE sfha_min_pct FLOAT64 DEFAULT 50;  -- min % of parcel area in a zone to count it (percentages are 0-100)

CREATE OR REPLACE TABLE `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_growth_flood_ct` AS
WITH growth AS (
  SELECT DISTINCT CAST(puid AS STRING) AS clip
  FROM `clgx-idap-bigquery-prd-a990.edr_ent_property_enriched.vw_edr_panoramiq_growth_indicators_v2`
),
parcel_rows AS (
  SELECT
    b.clip,
    CONCAT(b.state_code, b.cnty_code) AS fips,
    b.census_tract_id,
    g.clip IS NOT NULL AS is_growth,
    -- flood_zone_type_* is ignored: it contradicts zone codes (e.g. X tagged "SFHA with BFE")
    ARRAY<STRUCT<zone STRING, subtype STRING, pct FLOAT64>>[
      (UPPER(TRIM(b.flood_zone_1)), UPPER(b.flood_zone_subtype_1), b.flood_zone_percentage_1),
      (UPPER(TRIM(b.flood_zone_2)), UPPER(b.flood_zone_subtype_2), b.flood_zone_percentage_2),
      (UPPER(TRIM(b.flood_zone_3)), UPPER(b.flood_zone_subtype_3), b.flood_zone_percentage_3),
      (UPPER(TRIM(b.flood_zone_4)), UPPER(b.flood_zone_subtype_4), b.flood_zone_percentage_4),
      (UPPER(TRIM(b.flood_zone_5)), UPPER(b.flood_zone_subtype_5), b.flood_zone_percentage_5)
    ] AS zones
  FROM `clgx-gis-app-prd-364d.boundary.basicpublicrecord_parcel_addon` b
  LEFT JOIN growth g ON g.clip = b.clip
  WHERE CONCAT(b.state_code, b.cnty_code) IN UNNEST(target_fips)
    AND b.clip IS NOT NULL
),
-- One row per CLIP (a CLIP can span multiple parcel shapes)
parcels AS (
  SELECT
    clip,
    ANY_VALUE(fips)            AS fips,
    ANY_VALUE(census_tract_id) AS census_tract_id,
    LOGICAL_OR(is_growth)      AS is_growth,
    -- SFHA = 1% annual-chance zones (A*, V*); explicit list avoids matching e.g. "AREA NOT INCLUDED"
    MAX((SELECT IFNULL(SUM(z.pct), 0) FROM UNNEST(zones) z
         WHERE REGEXP_CONTAINS(z.zone, r'^(A|AE|AH|AO|AR|A99|A\d{1,2}|V|VE|V\d{1,2})$'))) AS sfha_pct,
    -- Coastal high-hazard (wave action / surge) zones
    MAX((SELECT IFNULL(SUM(z.pct), 0) FROM UNNEST(zones) z
         WHERE REGEXP_CONTAINS(z.zone, r'^(V|VE|V\d{1,2})$')))                         AS vzone_pct,
    -- Moderate = shaded X: 0.2% annual chance or levee-protected
    MAX((SELECT IFNULL(SUM(z.pct), 0) FROM UNNEST(zones) z
         WHERE z.zone = 'X'
           AND (z.subtype LIKE '%0.2 PCT%' OR z.subtype LIKE '%LEVEE%')))           AS moderate_pct,
    -- D = undetermined risk (not the same as low risk)
    MAX((SELECT IFNULL(SUM(z.pct), 0) FROM UNNEST(zones) z WHERE z.zone = 'D'))      AS dzone_pct
  FROM parcel_rows
  GROUP BY clip
),
tract_counts AS (
  SELECT
    fips,
    -- Assumes 11-digit GEOID or 6-digit TRACTCE; other formats yield NULL (check null_geoid_parcels below)
    CASE LENGTH(census_tract_id)
      WHEN 11 THEN census_tract_id
      WHEN 6  THEN CONCAT(fips, census_tract_id)
    END                                                         AS geoid,
    COUNT(*)                                                    AS total_parcels,
    COUNTIF(is_growth)                                          AS growth_parcels,
    COUNTIF(sfha_pct >= sfha_min_pct)                           AS sfha_parcels,
    COUNTIF(vzone_pct >= sfha_min_pct)                          AS vzone_parcels,
    COUNTIF(moderate_pct >= sfha_min_pct)                       AS moderate_parcels,
    COUNTIF(dzone_pct >= sfha_min_pct)                          AS dzone_parcels,
    COUNTIF(is_growth AND sfha_pct >= sfha_min_pct)             AS growth_sfha_parcels
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
  tc.sfha_parcels,
  tc.vzone_parcels,
  tc.moderate_parcels,
  tc.dzone_parcels,
  tc.growth_sfha_parcels,
  ROUND(SAFE_DIVIDE(tc.growth_parcels, tc.total_parcels), 3)          AS growth_share,
  ROUND(SAFE_DIVIDE(tc.sfha_parcels, tc.total_parcels), 3)            AS sfha_share,
  ROUND(SAFE_DIVIDE(tc.vzone_parcels, tc.total_parcels), 3)           AS vzone_share,
  ROUND(SAFE_DIVIDE(tc.moderate_parcels, tc.total_parcels), 3)        AS moderate_share,
  ROUND(SAFE_DIVIDE(tc.dzone_parcels, tc.total_parcels), 3)           AS dzone_share,
  -- "Watchlist" signal: share of new growth landing in the floodplain
  ROUND(SAFE_DIVIDE(tc.growth_sfha_parcels, tc.growth_parcels), 3)    AS growth_in_sfha_share,
  tc.total_parcels >= min_parcels                                     AS meets_min_parcels,
  ST_Y(ST_CENTROID(t.geometry))                                       AS ctr_lat,
  ST_X(ST_CENTROID(t.geometry))                                       AS ctr_lon,
  t.geometry
FROM tract_counts tc
LEFT JOIN tracts t USING (geoid)   -- LEFT JOIN so unmatched tract IDs surface as NULL geometry
ORDER BY tc.fips, growth_share DESC;

-- Sanity checks after the table is built
SELECT
  COUNTIF(geoid IS NULL)            AS null_geoid_tracts,
  SUM(IF(geoid IS NULL, total_parcels, 0)) AS null_geoid_parcels,
  COUNTIF(geometry IS NULL)         AS unmatched_geometry_tracts
FROM `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_growth_flood_ct`;
