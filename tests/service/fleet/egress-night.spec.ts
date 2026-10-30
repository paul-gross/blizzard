import { type EgressCopy, expect, stepUntil, test } from '../../harness/fixtures';

// A night of chunks, exported end to end. One forge, one hub and one runner carry six shapes: a plain build, a review
// bounce, a human gate, an ask answered by a person, a delivery conflict and an escalation, across two graphs that share
// the `build` and `deliver` node names. The hub exports it live as NDJSON, is restarted over the same record and exports
// it live again as Parquet into a second directory after the real `blizzard hub egress reset`, and a backfill of the
// night's window, given as UTC instants, writes a third copy. The night runs once, on the block's one held stack, and
// each test is one proof, reading the directories back the way a warehouse would and judging them against the hub's own
// record. Every pass of the hub and the host is stepped.

const RUN = Math.random().toString(16).slice(2, 10);
const TERMINAL = ['done', 'stopped', 'needs_human'];
const REPOSITORY = 'toy-api';
const RUNNER = 'runner-local';
const DEFAULT_GRAPH = 'default-delivery';
// The second graph reuses the default graph's `build` and `deliver` node names, so a station is told apart by its graph.
const LANE_GRAPH = `egress-night-lane-${RUN}`;
// The station built to be the slowest, on the graph whose shape carries it: its worker holds at a checkpoint for longer
// than any other step of the night lasts.
const SLOW_STATION = 'assemble';
const SLOW_HOLD_MS = 15_000;
const RESOLVER = `egress-night-resolver-${RUN}`;
const ASK_QUESTION = `EGRESS-ASK-${RUN}: which API style should the endpoint use?`;
const ASK_ANSWER = `EGRESS-ANSWER-${RUN}`;
const TRANSCRIPT_TOKEN = `EGRESS-TRANSCRIPT-${RUN}`;
const APPROVE_DESCRIPTION = `EGRESS-CHOICE-${RUN} ship it to delivery`;
const REJECT_DESCRIPTION = `EGRESS-CHOICE-${RUN} send it back to build`;
const DATASETS = ['steps', 'invocations'] as const;
const IDENTITY = { steps: 'step_key', invocations: 'usage_id' } as const;
type Dataset = (typeof DATASETS)[number];

const PASS_JUDGEMENT = "verdict('pass', 'committed; checks are green')\n";
const RETRIES = { max: 1, exhausted: 'escalate' };

/** A worker turn: `before`, a real commit adding `filename` on a branch of its own, then push and declare. */
const commitScript = (cli: string, branch: string, filename: string, message: string, before = ''): string =>
  [
    'import os, pathlib, subprocess',
    `print(${JSON.stringify(TRANSCRIPT_TOKEN)})`,
    before,
    "repo_path = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / 'toy-api')",
    `subprocess.run(['git', '-C', repo_path, 'switch', '-C', ${JSON.stringify(branch)}], check=True, capture_output=True)`,
    `(pathlib.Path(repo_path) / ${JSON.stringify(filename)}).write_text(${JSON.stringify(message + '\n')})`,
    "subprocess.run(['git', '-C', repo_path, 'add', '-A'], check=True, capture_output=True)",
    `subprocess.run(['git', '-C', repo_path, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', ${JSON.stringify(message)}], check=True, capture_output=True)`,
    "_commit = subprocess.run(['git', '-C', repo_path, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    `subprocess.run(['git', '-C', repo_path, 'push', '--force', 'origin', ${JSON.stringify(branch)}], check=True, capture_output=True)`,
    `subprocess.run([${JSON.stringify(cli)}, 'runner', 'artifact', 'commit', '--repo', 'toy-api', '--branch', ${JSON.stringify(branch)}, '--commit', _commit], check=True, capture_output=True)`,
    '',
  ].join('\n');

/** The hub's landing node; a conflict routes back to the graph's entry. */
const deliverNode = (entry: string) => ({
  executor: 'hub',
  action: 'land',
  poll_interval: 1,
  judgement: {
    choices: {
      landed: { description: 'Landed.', to: 'done' },
      conflict: { description: 'Conflict; back to the entry.', to: entry },
      failure: { description: 'Failed; back to the entry.', to: entry },
      'inherited-failure': { description: 'The base check stays red.', to: entry },
    },
  },
});

interface RunnerNode {
  executor: 'runner';
  prompt: string;
  judgement: { prompt: string; choices: Record<string, { description: string; to: string }> };
  retries: typeof RETRIES;
}

const buildNode = (prompt: string, to: string): RunnerNode => ({
  executor: 'runner',
  prompt,
  judgement: { prompt: PASS_JUDGEMENT, choices: { pass: { description: 'Committed and green.', to } } },
  retries: RETRIES,
});

/** The parts of a chunk's detail the night reads. */
interface ChunkDetail {
  status: string;
  graph_id: string;
  history: { epoch: number; from_node_name: string | null }[];
  usage: unknown[];
  bounces: unknown[];
  artifacts: { name: string; content?: string }[];
  decision: { decision_id: string };
  questions: { question_id: string }[];
  escalation: { takeover_command: string };
}

const graphOf = (nodes: Record<string, unknown>, entry: string, name = DEFAULT_GRAPH): string => JSON.stringify({ name, entry, nodes });

/** The newest copy of each identity, by `exported_at`. */
function newest(copies: EgressCopy[], identity: string): Map<string, EgressCopy> {
  const kept = new Map<string, EgressCopy>();
  for (const copy of copies) {
    const key = String(copy[identity]);
    const held = kept.get(key);
    if (held === undefined || (copy.exported_at as string) > (held.exported_at as string)) kept.set(key, copy);
  }
  return kept;
}

/** Each dataset's newest copies keyed by identity, with the one column that differs per copy dropped. */
function withoutExportTime(rows: Record<Dataset, EgressCopy[]>): Record<Dataset, Record<string, unknown>> {
  const out = {} as Record<Dataset, Record<string, unknown>>;
  for (const dataset of DATASETS) {
    out[dataset] = Object.fromEntries([...newest(rows[dataset], IDENTITY[dataset])].map(([key, copy]) => {
      const { exported_at: _dropped, ...rest } = copy;
      return [key, rest];
    }));
  }
  return out;
}

const bareLocal = (at: Date): string => {
  const two = (value: number): string => String(value).padStart(2, '0');
  return `${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())}T${two(at.getHours())}:${two(at.getMinutes())}:${two(at.getSeconds())}`;
};
const zoned = (at: Date): string => at.toISOString().replace(/\.\d{3}Z$/, 'Z');

type Fixtures = Pick<
  Parameters<Parameters<typeof test>[2]>[0],
  'api' | 'cli' | 'egress' | 'forge' | 'freshHub' | 'project' | 'repository' | 'runnerHost' | 'workerCheckpoint' | 'once'
>;

test.describe('the night, held on one stack', () => {
  test.describe.configure({ mode: 'default', timeout: 480_000 });
  test.use({
    isolation: 'fresh-hub',
    held: 'night',
    hubConfig: { egress: { directory: '${egress}', format: 'ndjson', settle_seconds: 0n, min_free_bytes: 0n } },
    runnerOnForge: [REPOSITORY],
    runnerConfig: { workspace: [{ id: 'workspace-local', repo: [{ name: REPOSITORY, url: `file://\${forgeOrigins}/blizzard/${REPOSITORY}.git` }] }] },
  });

  /** Runs the night on the held stack, once, and hands every proof what it needs to judge it. */
  const night = (fixtures: Fixtures) => {
    const { api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once } = fixtures;
    return once('night', async () => {
      const repo = await repository(REPOSITORY);
      const cliPath = runnerHost.cliBinary;
      const chunkIds: string[] = [];
      const titles: string[] = [];
      const bodies: string[] = [];
      const planted: Record<string, string[]> = {};
      const detail = async (chunkId: string): Promise<ChunkDetail> => (await api.get(`/api/chunks/${chunkId}`)).body as unknown as ChunkDetail;
      const stepTo = (chunkId: string, reached: (detail: ChunkDetail) => boolean = (d) => TERMINAL.includes(d.status)) =>
        stepUntil(runnerHost, () => detail(chunkId), reached, 400);
      const blizzard = async (args: string[]): Promise<string> => {
        const done = await cli(args);
        expect(done.code, `blizzard ${args.join(' ')} failed:\n${done.stderr}`).toBe(0);
        return done.stdout;
      };
      /** Mints the shape's graph, files its issue and ingests it onto that graph; the chunk is ready for the runner. */
      const ingest = async (graph: string, name: string): Promise<string> => {
        const minted = await api.post('/api/graphs', { data: { definition_yaml: graph } });
        expect(minted.status, JSON.stringify(minted.body)).toBe(201);
        const title = `EGRESS-TITLE-${RUN}-${name}`;
        const body = `EGRESS-BODY-${RUN}-${name}`;
        const issue = await forge.createIssue(`${repo.owner}/${repo.name}`, { title, body });
        const ingested = await api.post('/api/chunks', { data: { tokens: [`${repo.tokenPrefix}:${issue.number}`], project: project.slug } });
        expect(ingested.status, JSON.stringify(ingested.body)).toBe(201);
        const chunkId = ingested.body.chunk_id as string;
        if (minted.body.name !== DEFAULT_GRAPH) {
          expect((await api.patch(`/api/chunks/${chunkId}`, { data: { graph_id: minted.body.graph_id } })).status).toBe(202);
        }
        expect((await detail(chunkId)).graph_id, `${name} is not on its own graph`).toBe(minted.body.graph_id);
        expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
        chunkIds.push(chunkId);
        titles.push(title);
        bodies.push(body);
        return chunkId;
      };
      const exported = async (): Promise<void> => {
        await api.sweep('egress', { allTenants: true });
      };
      const historySteps = async (): Promise<Set<string>> => {
        const steps = new Set<string>();
        for (const chunkId of chunkIds) {
          for (const entry of (await detail(chunkId)).history) {
            if (entry.from_node_name) steps.add(`${chunkId}/${entry.epoch}/${entry.from_node_name}`);
          }
        }
        return steps;
      };
      const heldSteps = (copies: EgressCopy[]): Set<string> => new Set([...newest(copies, 'step_key').values()].map((r) => `${r.chunk_id}/${r.epoch}/${r.node_name}`));

      // The export's first pass only anchors its cursors, so it runs before any work is done.
      await exported();
      const since = new Date(Math.floor(Date.now() / 1000) * 1000 - 5000);

      // -- a plain build, whose worker is held at a checkpoint for longer than any other step of the night lasts --
      const plain = await ingest(
        graphOf({ [SLOW_STATION]: buildNode(commitScript(cliPath, 'feature/night-plain', 'PLAIN.md', 'a plain build', workerCheckpoint.snippet('slow')), 'deliver'), deliver: deliverNode(SLOW_STATION) }, SLOW_STATION, LANE_GRAPH),
        'plain',
      );
      let arrived = false;
      void workerCheckpoint.reached('slow').then(() => { arrived = true; });
      await stepUntil(runnerHost, async () => arrived, Boolean, 400);
      const holdFrom = Date.now();
      await expect.poll(() => Date.now(), { timeout: SLOW_HOLD_MS + 15_000 }).toBeGreaterThan(holdFrom + SLOW_HOLD_MS);
      workerCheckpoint.release('slow');
      expect((await stepTo(plain)).status).toBe('done');

      // -- a review bounce --
      const reviewJudgement = [
        'import pathlib',
        "m = pathlib.Path('.review-count')",
        'n = (int(m.read_text()) if m.exists() else 0) + 1',
        'm.write_text(str(n))',
        'if n == 1:',
        "    verdict('fail', 'BLOCKING: guard the empty input before delivery')",
        'else:',
        "    verdict('pass', 'findings addressed; ready to deliver')",
        '',
      ].join('\n');
      const bounceBuild = buildNode(commitScript(cliPath, 'feature/night-bounce', 'BOUNCE.md', 'a build pass'), 'review') as RunnerNode;
      bounceBuild.judgement.choices.fail = { description: 'Incomplete.', to: 'build' };
      const bounce = await ingest(
        graphOf({
          build: bounceBuild,
          review: {
            executor: 'runner',
            prompt: 'pass\n',
            session: 'fresh',
            produces: ['review-findings'],
            judgement: {
              prompt: reviewJudgement,
              choices: {
                pass: { description: 'Passes review.', to: 'deliver' },
                fail: { description: 'Blocking issues found.', to: 'build', prompt_addendum: commitScript(cliPath, 'feature/night-bounce', 'ADDRESSED.md', 'address review findings') },
              },
            },
            retries: RETRIES,
          },
          deliver: deliverNode('build'),
        }, 'build'),
        'bounce',
      );
      expect((await stepTo(bounce)).status).toBe('done');

      // -- a human gate --
      const gate = await ingest(
        graphOf({
          build: buildNode(commitScript(cliPath, 'feature/night-gate', 'GATED.md', 'a change awaiting sign-off'), 'approve-gate'),
          'approve-gate': {
            executor: 'runner',
            judgement: { by: 'human', choices: { approve: { description: APPROVE_DESCRIPTION, to: 'deliver' }, reject: { description: REJECT_DESCRIPTION, to: 'build' } } },
          },
          deliver: deliverNode('build'),
        }, 'build'),
        'gate',
      );
      expect((await stepTo(gate, (d) => d.status === 'waiting_on_human' || TERMINAL.includes(d.status))).status).toBe('waiting_on_human');
      const decisionId = (await detail(gate)).decision.decision_id;
      await blizzard(['hub', 'decision', 'resolve', decisionId, 'approve', '--by', RESOLVER, '--json']);
      expect((await stepTo(gate)).status).toBe('done');
      planted['gate choice description'] = [APPROVE_DESCRIPTION, REJECT_DESCRIPTION];
      planted['resolver login'] = [RESOLVER];

      // -- an ask answered by a person --
      const ask = await ingest(
        graphOf({ clarify: buildNode(`ask(${JSON.stringify(ASK_QUESTION)}, ["rest", "graphql"])\n`, 'deliver'), deliver: deliverNode('clarify') }, 'clarify'),
        'ask',
      );
      expect((await stepTo(ask, (d) => d.status === 'waiting_on_human' || TERMINAL.includes(d.status))).status).toBe('waiting_on_human');
      const questionId = (await detail(ask)).questions[0]!.question_id;
      await blizzard(['hub', 'question', 'answer', questionId, commitScript(cliPath, 'feature/night-ask', 'ASKED.md', 'resolve the ask', `# ${ASK_ANSWER}\n`), '--by', RESOLVER]);
      expect((await stepTo(ask)).status).toBe('done');
      planted['ask question'] = [ASK_QUESTION];
      planted['ask answer'] = [ASK_ANSWER];

      // -- a delivery conflict, armed on the forge until the chunk has bounced off it --
      const conflict = await ingest(
        graphOf({ build: buildNode(commitScript(cliPath, 'feature/night-conflict', 'CONFLICTED.md', 'a change the armed conflict lever rejects'), 'deliver'), deliver: deliverNode('build') }, 'build', LANE_GRAPH),
        'conflict',
      );
      const forgeRepo = `${repo.owner}/${repo.name}`;
      expect((await forge.call('POST', '/_levers/merge_conflict', { repo: forgeRepo })).status).toBe(200);
      try {
        await stepTo(conflict, (d) => d.bounces.length > 0 || TERMINAL.includes(d.status));
      } finally {
        expect((await forge.call('DELETE', `/_levers/merge_conflict?repo=${encodeURIComponent(forgeRepo)}`)).status).toBe(200);
      }
      expect((await detail(conflict)).bounces.length, 'the armed conflict never bounced the chunk').toBeGreaterThan(0);
      expect((await stepTo(conflict)).status).toBe('done');
      const envelope = (await detail(conflict)).artifacts.filter((artifact) => artifact.name === 'bounce-envelope');
      expect(envelope[0]?.content, 'the bounce left no envelope').toBeTruthy();
      planted['bounce envelope'] = envelope[0]!.content!.split('\n').filter((line) => line.trim().length > 12);

      // -- an escalation: the judgement emits no verdict, so every attempt fails and the retry budget exhausts --
      const escalationBuild = buildNode('pass\n', 'deliver');
      escalationBuild.judgement.prompt = 'pass\n';
      const escalation = await ingest(graphOf({ build: escalationBuild, deliver: deliverNode('build') }, 'build'), 'escalation');
      expect((await stepTo(escalation)).status).toBe('needs_human');
      planted['takeover command'] = [(await detail(escalation)).escalation.takeover_command];

      planted['work item title'] = titles;
      planted['work item body'] = bodies;
      planted['worker transcript output'] = [TRANSCRIPT_TOKEN];
      const leases = (await runnerHost.runners[RUNNER]!.leases()).body.items.filter((lease) => chunkIds.includes(lease.chunk_id));
      planted['lease token'] = leases.flatMap((lease) => {
        try {
          return [runnerHost.leaseToken(lease.lease_id)];
        } catch {
          return [];
        }
      });

      // -- the night, exported live as NDJSON --
      const hubUsage = async (): Promise<number> => {
        let total = 0;
        for (const chunkId of chunkIds) total += (await detail(chunkId)).usage.length;
        return total;
      };
      let ndjsonRows!: Record<Dataset, EgressCopy[]>;
      await expect.poll(async () => {
        await exported();
        ndjsonRows = { steps: await egress.copies('steps'), invocations: await egress.copies('invocations') };
        const stepsHeld = heldSteps(ndjsonRows.steps);
        const missing = [...(await historySteps())].filter((step) => !stepsHeld.has(step));
        return missing.length === 0 && newest(ndjsonRows.invocations, 'usage_id').size === (await hubUsage());
      }, { message: 'the night in the NDJSON export', timeout: 120_000 }).toBe(true);

      // -- the same record, exported live again as Parquet into a second directory --
      await freshHub.stop();
      await freshHub.start({ egress: { directory: '${egress}-parquet', format: 'parquet' } });
      const parquet = egress.at(`${egress.directory}-parquet`, 'parquet');
      for (const dataset of DATASETS) await blizzard(['hub', 'egress', 'reset', '--dataset', dataset, '--to', bareLocal(since)]);
      let parquetRows!: Record<Dataset, EgressCopy[]>;
      await expect.poll(async () => {
        await exported();
        if (!parquet.exports('steps') || !parquet.exports('invocations')) return false;
        parquetRows = { steps: await parquet.copies('steps'), invocations: await parquet.copies('invocations') };
        return JSON.stringify(withoutExportTime(parquetRows)) === JSON.stringify(withoutExportTime(ndjsonRows));
      }, { message: 'the live Parquet export to hold the night', timeout: 120_000 }).toBe(true);

      // The window closes on a whole second past every fact of the night, never in the future.
      const mark = Date.now();
      await expect.poll(() => Date.now(), { timeout: 15_000 }).toBeGreaterThan(mark + 1100);
      const until = new Date(Math.floor(Date.now() / 1000) * 1000);
      await blizzard(['hub', 'egress', 'backfill', '--since', zoned(since), '--until', zoned(until)]);
      const backfilled = { steps: await parquet.copies('steps', { files: 'backfill' }), invocations: await parquet.copies('invocations', { files: 'backfill' }) };
      const liveParquet = { steps: await parquet.copies('steps', { files: 'live' }), invocations: await parquet.copies('invocations', { files: 'live' }) };

      return { chunkIds, planted, since, until, ndjsonRows: { steps: await egress.copies('steps'), invocations: await egress.copies('invocations') }, parquetRows: liveParquet, backfilled, parquet, detail, blizzard, window: ['--since', zoned(since), '--until', zoned(until)] };
    });
  };

  test('test_both_live_exports_hold_the_same_newest_rows', async ({ api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once }) => {
    const fixtures = { api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once };
    const exported = await night(fixtures);
    expect(withoutExportTime(exported.parquetRows)).toEqual(withoutExportTime(exported.ndjsonRows));
    for (const dataset of DATASETS) expect(newest(exported.ndjsonRows[dataset], IDENTITY[dataset]).size, `${dataset} is empty`).toBeGreaterThan(0);
  });

  test('test_both_exports_hold_the_hubs_record', async ({ api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once }) => {
    const fixtures = { api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once };
    const exported = await night(fixtures);
    const spend = new Map<string, Record<string, number>>(
      (await exported.blizzard(['hub', 'analytics', 'summary', 'spend-chunks', '--ndjson'])).split('\n').filter(Boolean).map((line) => {
        const row = JSON.parse(line) as Record<string, number> & { chunk_id: string };
        return [row.chunk_id, row] as const;
      }),
    );
    for (const [label, rows] of [['ndjson', exported.ndjsonRows], ['parquet', exported.parquetRows]] as const) {
      const steps = [...newest(rows.steps, 'step_key').values()];
      const held = new Set(steps.map((r) => `${r.chunk_id}/${r.epoch}/${r.node_name}`));
      for (const chunkId of exported.chunkIds) {
        const history = (await exported.detail(chunkId)).history.filter((entry) => entry.from_node_name);
        for (const entry of history) expect(held.has(`${chunkId}/${entry.epoch}/${entry.from_node_name}`), `${label}: a step of ${chunkId} is missing from the export`).toBe(true);
      }
      const invocations = [...newest(rows.invocations, 'usage_id').values()];
      for (const chunkId of exported.chunkIds) {
        const mine = invocations.filter((r) => r.chunk_id === chunkId);
        const usage = (await exported.detail(chunkId)).usage;
        expect(mine.length, `${label}: ${chunkId} usage facts`).toBe(usage.length);
        for (const column of ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_create_tokens']) {
          expect(mine.reduce((sum, r) => sum + Number(r[column]), 0), `${label}: ${chunkId} ${column}`).toBe(spend.get(chunkId)?.[column] ?? 0);
        }
        const billed = mine.reduce((sum, r) => sum + (r.cost_billed_usd === null ? 0 : Number(r.cost_billed_usd)), 0);
        expect(billed, `${label}: ${chunkId} billed cost`).toBeCloseTo(spend.get(chunkId)?.cost_usd ?? 0, 6);
      }
    }
  });

  test('test_both_exports_name_the_runner_by_its_minted_id_and_its_name', async ({ api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once }) => {
    const fixtures = { api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once };
    const exported = await night(fixtures);
    const runner = fixtures.runnerHost.runners[RUNNER]!;
    expect(runner.id.startsWith('rn_')).toBe(true);
    for (const [label, rows] of [['ndjson', exported.ndjsonRows], ['parquet', exported.parquetRows]] as const) {
      const ran = new Set([...newest(rows.steps, 'step_key').values()].filter((r) => r.runner_id !== null).map((r) => `${r.runner_id}/${r.runner_name}`));
      expect([...ran], `${label} steps`).toEqual([`${runner.id}/${runner.name}`]);
      const billed = new Set([...newest(rows.invocations, 'usage_id').values()].map((r) => `${r.runner_id}/${r.runner_name}`));
      expect([...billed], `${label} invocations`).toEqual([`${runner.id}/${runner.name}`]);
    }
  });

  test('test_nothing_planted_leaves', async ({ api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once }) => {
    const fixtures = { api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once };
    const exported = await night(fixtures);
    expect(Object.keys(exported.planted)).toEqual(expect.arrayContaining([
      'work item title', 'work item body', 'ask question', 'ask answer', 'gate choice description', 'resolver login',
      'bounce envelope', 'worker transcript output', 'takeover command', 'lease token',
    ]));
    for (const [label, reader] of [['ndjson', fixtures.egress], ['parquet', exported.parquet]] as const) {
      const documents = reader.documents();
      const texts = [...(await reader.rowTexts('steps')), ...(await reader.rowTexts('invocations')), ...documents.manifests, ...documents.schemas];
      expect(texts.length, `${label}: the scan read no rows`).toBeGreaterThan(DATASETS.length);
      for (const [kind, values] of Object.entries(exported.planted)) {
        expect(values.length > 0 && values.every(Boolean), `nothing was planted for ${kind}: the scan would pass vacuously`).toBe(true);
        for (const value of values) expect(texts.some((text) => text.includes(value)), `${label} carries a planted ${kind}: ${value}`).toBe(false);
      }
    }
  });

  test('test_a_backfill_of_the_window_equals_the_live_export', async ({ api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once }) => {
    const fixtures = { api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once };
    const exported = await night(fixtures);
    expect(Object.values(exported.backfilled).some((rows) => rows.length > 0), 'the backfill wrote nothing').toBe(true);
    expect(withoutExportTime(exported.backfilled)).toEqual(withoutExportTime(exported.parquetRows));
  });

  test('test_recipes_agree_with_the_hubs_analytics', async ({ api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once }) => {
    const fixtures = { api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once };
    const exported = await night(fixtures);
    const spend = JSON.parse(await exported.blizzard(['hub', 'analytics', 'summary', 'spend-nodes', ...exported.window, '--json'])).spend as { key: string; cost_usd: number | null; estimated_cost_usd: number | null }[];
    const station = new Map([...newest(exported.parquetRows.steps, 'step_key').values()].map((r) => [String(r.node_id), `${r.graph_name}/${r.node_name}`]));
    const hubCost = new Map<string, [number, number]>();
    for (const row of spend) {
      const at = station.get(row.key)!;
      const held = hubCost.get(at) ?? [0, 0];
      hubCost.set(at, [held[0] + (row.cost_usd ?? 0), held[1] + (row.estimated_cost_usd ?? 0)]);
    }
    for (const [label, reader] of [['ndjson', fixtures.egress], ['parquet', exported.parquet]] as const) {
      const recipeCost = new Map<string, [number, number]>();
      for (const row of await reader.recipe('cost-by-node-by-day')) {
        const at = `${row.graph_name}/${row.node_name}`;
        const held = recipeCost.get(at) ?? [0, 0];
        recipeCost.set(at, [held[0] + Number(row.billed_usd ?? 0), held[1] + Number(row.estimated_usd ?? 0)]);
      }
      // A station with no usage (a gate, the hub's own steps) has a cost row but no spend row.
      for (const at of hubCost.keys()) expect(recipeCost.has(at), `${label}: ${at}`).toBe(true);
      for (const [at, [billed, estimated]] of recipeCost) {
        const [expectedBilled, expectedEstimated] = hubCost.get(at) ?? [0, 0];
        expect(billed, `${label} billed ${at}`).toBeCloseTo(expectedBilled, 6);
        expect(estimated, `${label} estimated ${at}`).toBeCloseTo(expectedEstimated, 6);
      }
      const [slowest] = await reader.recipe('slowest-station-of-the-week');
      expect([slowest?.graph_name, slowest?.node_name], label).toEqual([LANE_GRAPH, SLOW_STATION]);
    }
  });

  test('test_cost_by_node_splits_a_shared_node_name_by_graph', async ({ api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once }) => {
    const fixtures = { api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once };
    const exported = await night(fixtures);
    for (const [label, reader] of [['ndjson', fixtures.egress], ['parquet', exported.parquet]] as const) {
      const stations = (await reader.recipe('cost-by-node-by-day')).map((row) => [row.graph_name as string, row.node_name as string] as const);
      for (const node of ['build', 'deliver']) {
        const graphs = new Set(stations.filter(([, name]) => name === node).map(([graph]) => graph));
        expect([...graphs].sort(), `${label} ${node}`).toEqual([DEFAULT_GRAPH, LANE_GRAPH].sort());
      }
    }
  });

  test('test_published_newest_views_return_the_newest_copies', async ({ api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once }) => {
    const fixtures = { api, cli, egress, forge, freshHub, project, repository, runnerHost, workerCheckpoint, once };
    const exported = await night(fixtures);
    for (const [label, reader] of [['ndjson', fixtures.egress], ['parquet', exported.parquet]] as const) {
      for (const dataset of DATASETS) {
        const view = await reader.query(dataset, `SELECT ${IDENTITY[dataset]}::VARCHAR AS id FROM ${dataset}_newest`);
        const expected = [...newest(await reader.copies(dataset), IDENTITY[dataset]).keys()].sort();
        expect(view.map((row) => row.id as string).sort(), `${label} ${dataset}`).toEqual(expected);
      }
    }
  });
});
