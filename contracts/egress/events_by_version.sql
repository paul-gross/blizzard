SELECT *
FROM (
  SELECT m.*, row_number() OVER (PARTITION BY m.derivation_id, m.kind, m.turn_path, m.occurrence ORDER BY m.exported_at DESC) AS copy_rank
  FROM (
    SELECT
      e.*,
      max(CASE WHEN e.record_type = 'derivation' THEN e.derived_at END) OVER (PARTITION BY e.segment_id, e.extractor_version) AS newest_derived_at,
      max(CASE WHEN e.record_type = 'dropped' THEN e.dropped_at END) OVER (PARTITION BY e.segment_id) AS last_dropped_at
    FROM events AS e
  ) AS m
  WHERE m.record_type = 'event'
    AND m.derived_at = m.newest_derived_at
    AND (m.last_dropped_at IS NULL OR m.last_dropped_at <= m.newest_derived_at)
) AS ranked
WHERE copy_rank = 1
