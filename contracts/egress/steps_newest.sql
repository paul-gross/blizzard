SELECT *
FROM (
  SELECT s.*, row_number() OVER (PARTITION BY step_key ORDER BY exported_at DESC) AS copy_rank
  FROM steps AS s
) AS ranked
WHERE copy_rank = 1
