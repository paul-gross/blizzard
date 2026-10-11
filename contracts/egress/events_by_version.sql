SELECT *
FROM (
  SELECT m.*, row_number() OVER (PARTITION BY m.segment_tenant_id, m.derivation_id, m.kind, m.turn_path, m.occurrence ORDER BY m.exported_at DESC) AS copy_rank
  FROM (
    SELECT
      t.*,
      max(CASE WHEN t.record_type = 'derivation' THEN t.derived_at END) OVER (PARTITION BY t.segment_tenant_id, t.segment_id, t.extractor_version) AS newest_derived_at,
      max(CASE WHEN t.record_type = 'dropped' THEN t.dropped_at END) OVER (PARTITION BY t.segment_tenant_id, t.segment_id) AS last_dropped_at
    FROM (
      -- A row of a file written before the upgrade to tenants has no tenant_id and belongs to the tenant the upgrade
      -- carried the hub's data into. That tenant was minted at the upgrade, so its time-ordered id is the earliest of
      -- any relation holding a row it wrote since; a relation holding none gives such rows to its earliest tenant.
      SELECT e.*, coalesce(e.tenant_id, min(e.tenant_id) OVER ()) AS segment_tenant_id
      FROM events AS e
    ) AS t
  ) AS m
  WHERE m.record_type = 'event'
    AND m.derived_at = m.newest_derived_at
    AND (m.last_dropped_at IS NULL OR m.last_dropped_at <= m.newest_derived_at)
) AS ranked
WHERE copy_rank = 1
