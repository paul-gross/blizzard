SELECT *
FROM (
  SELECT i.*, row_number() OVER (PARTITION BY usage_id ORDER BY exported_at DESC) AS copy_rank
  FROM invocations AS i
) AS ranked
WHERE copy_rank = 1
