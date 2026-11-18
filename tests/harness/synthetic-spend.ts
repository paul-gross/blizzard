import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { BLIZZARD_ROOT, binDir } from './env';
import type { RunnerHost } from './runner-host';

/** Insert one invocation into a disposable host's store. */
export function seedSyntheticSpend(host: RunnerHost, runnerId: string, dollars: number): void {
  const seed = `from datetime import UTC, datetime\nfrom blizzard.runner.harness.usage import UsageSample\nfrom tests.support.runner_fakes import make_store, record_usage\nimport sys\ns = make_store('sqlite:///' + sys.argv[1], sys.argv[2])\nrecord_usage(s, lease_id='synthetic', chunk_id='synthetic', node_id='build', epoch=1, generation=1, sample=UsageSample('spawn', 'model', 0, 0, 0, 0, float(sys.argv[3])), recorded_at=datetime.now(UTC))`;
  execFileSync(join(binDir(), 'python'), ['-c', seed, join(host.dir, 'data', 'host.db'), runnerId, String(dollars)], { cwd: BLIZZARD_ROOT });
}
