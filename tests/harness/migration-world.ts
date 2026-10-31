import type { HubApi } from './client/clients';
import type { FiledIssue, Repository } from './factories';
import type { RunnerHosts } from './runner-world';
import { stepUntil } from './runner-steps';

const branch = 'feature/scripted-build';

/** The same real-hub, real-runner migration world used by the fleet trace and the served-board assertion. */
export async function migratedChunk(world: {
  api: HubApi;
  issue: (title: string, options?: { repository?: Repository }) => Promise<FiledIssue>;
  repository: (name?: string) => Promise<Repository>;
  runnerHosts: RunnerHosts;
}) {
  const { api, issue, repository, runnerHosts } = world;
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  const cli = JSON.stringify(host.cliBinary);
  const build = `import os, pathlib, subprocess\nrepo = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repo.name)}\nsubprocess.run(['git', '-C', str(repo), 'switch', '-C', '${branch}'], check=True)\n(repo / 'LANDED.md').write_text('landed by the mock harness after a migration\\n')\nsubprocess.run(['git', '-C', str(repo), 'add', '-A'], check=True)\nsubprocess.run(['git', '-C', str(repo), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: land a change after migrating graphs'], check=True)\nsha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()\nsubprocess.run(['git', '-C', str(repo), 'push', 'origin', '${branch}'], check=True)\nsubprocess.run([${cli}, 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repo.name)}, '--branch', '${branch}', '--commit', sha], check=True)\n`;
  const target = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify({ name: 'triage-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt: build, judgement: { prompt: "verdict('pass', 'committed the change; checks are green')\n", choices: { pass: { description: 'Committed.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, judgement: { choices: { landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' }, failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check red.', to: 'build' } } } },
  } }) } });
  if (target.status !== 201) throw new Error(`target graph mint answered ${target.status}: ${JSON.stringify(target.body)}`);
  const source = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt: 'pass\n', judgement: { prompt: "verdict('migrate', 'hand the chunk to triage-delivery')\n", choices: { migrate: { description: 'Hand off.', to: 'graph:triage-delivery' } } }, retries: { max: 1, exhausted: 'escalate' } },
  } }) } });
  if (source.status !== 201) throw new Error(`source graph mint answered ${source.status}: ${JSON.stringify(source.body)}`);
  const { chunk } = await issue('migration', { repository: repo });
  const promoted = await api.post(`/api/chunks/${chunk.chunk_id}/promote`);
  if (promoted.status !== 202) throw new Error(`promotion answered ${promoted.status}`);
  const status = await stepUntil(host, async () => (await api.get(`/api/chunks/${chunk.chunk_id}`)).body.status, (value) => ['done', 'needs_human', 'stopped'].includes(value));
  return { repo, host, chunk, status, targetGraphId: target.body.graph_id as string, sourceGraphId: source.body.graph_id as string };
}
