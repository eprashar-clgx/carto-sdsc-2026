CREATE OR REPLACE TABLE `clgx-gis-app-dev-06e3.work_eprashar.carto_sdsc_growth_clip` AS 
SELECT 
  CAST(a.puid AS STRING) AS clip,
  b.parcel_shape_id,
  a.growth_stage,
  ST_CENTROID(b.geometry) AS pt
FROM `clgx-idap-bigquery-prd-a990.edr_ent_property_enriched.vw_edr_panoramiq_growth_indicators_v2` a
JOIN `clgx-gis-app-prd-364d.boundary.basicpublicrecord_parcel_addon` b
ON CAST(a.puid AS STRING) = b.clip
WHERE CONCAT(b.state_code,b.cnty_code) IN ('06065', '12071','48201')