SELECT *
FROM (
  SELECT m.*, row_number() OVER (PARTITION BY m.segment_tenant_id, m.derivation_id, m.kind, m.turn_path, m.occurrence ORDER BY m.exported_at DESC) AS copy_rank
  FROM (
    SELECT
      t.*,
      max(CASE WHEN t.record_type = 'derivation' THEN t.derived_at END) OVER (PARTITION BY t.segment_tenant_id, t.segment_id, t.extractor_version) AS newest_derived_at,
      max(CASE WHEN t.record_type = 'dropped' THEN t.dropped_at END) OVER (PARTITION BY t.segment_tenant_id, t.segment_id) AS last_dropped_at
    FROM (
      SELECT e.*, coalesce(e.tenant_id, min(e.tenant_id) OVER (PARTITION BY e.segment_id)) AS segment_tenant_id
      FROM events AS e
    ) AS t
  ) AS m
  WHERE m.record_type = 'event'
    AND m.derived_at = m.newest_derived_at
    AND (m.last_dropped_at IS NULL OR m.last_dropped_at <= m.newest_derived_at)
) AS ranked
WHERE copy_rank = 1
