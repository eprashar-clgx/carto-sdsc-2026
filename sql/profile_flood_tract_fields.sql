
-- 3) parcel_essentials CRS: rows per parcel_shape_id (history table -> snapshots?) and CRS range.
--    If max_rows_per_shape > 1, find the snapshot/date column and set it in growth_crs_tracts.sql.
SELECT
  COUNT(*)                          AS n_rows,
  COUNT(DISTINCT parcel_shape_id)   AS n_shapes,
  MAX(cnt)                          AS max_rows_per_shape,
  MIN(min_crs)                      AS min_crs,
  MAX(max_crs)                      AS max_crs,
  SUM(null_crs)                     AS null_crs_rows
FROM (
  SELECT parcel_shape_id, COUNT(*) AS cnt, MIN(crs) AS min_crs, MAX(crs) AS max_crs, COUNTIF(crs IS NULL) AS null_crs
  FROM `clgx-idap-bigquery-prd-a990.edr_ent_property_fulfillment_hist.parcel_essentials_dsf`
  WHERE parcel_shape_id IN (
    SELECT parcel_shape_id
    FROM `clgx-gis-app-prd-364d.boundary.basicpublicrecord_parcel_addon`
    WHERE CONCAT(state_code, cnty_code) IN UNNEST(target_fips)
  )
  GROUP BY parcel_shape_id
);
