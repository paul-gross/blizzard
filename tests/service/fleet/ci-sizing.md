# Fleet service-tier sizing

The full CI run on 2026-10-30 measured the two Playwright shard jobs at 12m10s and 6m17s, including provisioning and setup. Its slowest required gate job, pytest shard 1/4, took 20m15s; the Playwright step has a 30-minute ceiling. A runner host costs approximately 6.6s to initialize and 13s for a whole test, on top of the serial e2e file shares in the fleet roster.

| Incoming file | Existing e2e share | Cases | Host/test allowance | Estimated addition |
| --- | ---: | ---: | ---: | ---: |
| hub actions | 52s | 3 | 59s | 111s |
| event log | 82s | 1 | 20s | 102s |
| flow record | 56s | 1 | 20s | 76s |
| session modes | 68s | 2 | 39s | 107s |
| resume preamble | 71s | 2 | 39s | 110s |
| review findings delivery | 39s | 1 | 20s | 59s |
| garden routine | 111s | 1 | 20s | 131s |
| ideation | 155s | 1 | 20s | 175s |

The total upper-bound addition is about 14m31s. With two shards, a skewed placement could push the 12m10s shard over the 20m15s gate and possibly the step ceiling. Four shards distribute the default-profile fleet cases alongside the existing hub, runner, and harness tests. `actions` is serialized on one worker: its existing profile tests and harness-actions check precede or overlap the three incoming files, whose combined estimate is 6m28s (111 + 102 + 175 seconds). The default-profile fleet additions sum to 8m03s (76 + 107 + 110 + 59 + 131 seconds), spread across four shards. Even placing the entire serialized actions addition on the old slow shard yields 18m38s, below the slowest required gate job; no shard approaches 30 minutes.

The integrated local run (all service projects plus harness projects, 10 workers) completed in 6m36s, with both long cases reaching the old 90-second per-case timeout under contention. With a 240-second bound and the two long cases selected together, garden took 1m42s and ideation 2m24s; the run completed in 2m48s. The other selected cases finished between 9 and 33 seconds; the host startup and teardown add roughly 15–22 seconds per selected project under concurrent load.

The exact four-shard Playwright selection, run locally with `CI=1` (two workers per shard) and a pinned collector, passed every shard. The observed shard times exclude dependency provisioning and are therefore **not** GitHub job durations:

| Shard | Tests | Local Playwright wall time | Distinguishing fleet work |
| --- | ---: | ---: | --- |
| 1/4 | 122 | 6m45s | Hub and runner service tests |
| 2/4 | 120 | 12m06s | All eight sources' fleet cases and runner service tests |
| 3/4 | 120 | 3m25s | Harness and runner-world checks |
| 4/4 | 114 | 2m30s | Runner-world checks |

The skew is greater than the per-file sum divided by four: Playwright partitions by test count, placing all eight ports on shard 2, and that shard's actions profile executes serially. The measured local 12m06s leaves 17m54s before the 30-minute Playwright step limit; compare its eventual CI job duration with the prior gate's 20m15s rather than treating a local pass as the CI speed guard. The actual four-shard CI job durations remain to be recorded on a branch run.

The selected `service-fleet-actions` ideation run took 1.5m for the case and 1.7m including shared-hub setup/teardown, below the 175s per-file estimate. This is a local project timing, not a measured CI shard duration.
