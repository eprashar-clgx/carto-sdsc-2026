-- Run before growth_flood_tracts.sql to confirm value formats.
DECLARE target_fips ARRAY<STRING> DEFAULT ['12071','48201','06065'];  -- Lee, Harris, Riverside

-- 1) census_tract_id format: expect 11-digit GEOID; anything else needs handling in the main query
SELECT
  LENGTH(census_tract_id)     AS id_len,
  ANY_VALUE(census_tract_id)  AS example,
  COUNT(*)                    AS n
FROM `clgx-gis-app-prd-364d.boundary.basicpublicrecord_parcel_addon`
WHERE CONCAT(state_code, cnty_code) IN UNNEST(target_fips)
GROUP BY id_len
ORDER BY n DESC;

-- 2) flood zone codes/types and percentage units (0-1 vs 0-100)
SELECT
  CONCAT(state_code, cnty_code) AS fips,
  flood_zone_1,
  flood_zone_type_1,
  flood_zone_subtype_1,
  COUNT(*)                      AS n,
  MIN(flood_zone_percentage_1)  AS min_pct,
  MAX(flood_zone_percentage_1)  AS max_pct
FROM `clgx-gis-app-prd-364d.boundary.basicpublicrecord_parcel_addon`
WHERE CONCAT(state_code, cnty_code) IN UNNEST(target_fips)
GROUP BY 1, 2, 3, 4
ORDER BY fips, n DESC;
