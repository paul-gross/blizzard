import { expect, stepUntil, test } from '../../harness/fixtures';

// The exported `events` dataset, end to end. Two chunks across both mock harnesses read files, invoke a skill and spawn
// an agent whose child session reads a file; one transcript is extended by a resumed session. The dictionary's
// `events_current` view over the exported files is judged against the hub's own `analytics summary` counts, through a
// forced re-derivation and a real `transcript reship`. Every pass of the hub and the host is stepped.

const RUN = Math.random().toString(16).slice(2, 10);
const PATH_KEY = `egress-events-key-${RUN}`;
const PLANTED_INPUT = `EGRESS-TOOL-INPUT-${RUN}`;
const PLANTED_OUTPUT = `EGRESS-TOOL-OUTPUT-${RUN}`;
const PLANTED_PROMPT = `EGRESS-PROMPT-${RUN}`;
const ASK_QUESTION = `EGRESS-EVENTS-ASK-${RUN}: which style?`;
const REVIEW_SESSION = 'events-review';
const REVIEW_MODEL = 'mock-provider/opencode-e2e';

// What each node reads, relative to the worker's working directory.
const BUILD_READS = ['toy-api/src/main.py', 'toy-api/docs/build-notes.md'];
const REVIEW_READS = ['toy-api/src/main.py', 'toy-api/src/review-target.py'];
const CHILD_READS = ['toy-api/src/deep-dive.py'];
const ASK_BEFORE = ['toy-api/docs/ask-before.md'];
const ASK_AFTER = ['toy-api/docs/ask-after.md'];
const PASS = "verdict('pass', 'committed; checks are green')\n";
const PRELUDE = 'import os\n_cwd = os.getcwd()\n';

const read = (tool: string, key: string, file: string): string =>
  `tool_call(${JSON.stringify(tool)}, {${JSON.stringify(key)}: os.path.join(_cwd, ${JSON.stringify(file)}), 'note': ${JSON.stringify(PLANTED_INPUT)}}, ${JSON.stringify(PLANTED_OUTPUT)})\n`;

const buildScript = (): string =>
  PRELUDE + BUILD_READS.map((file) => read('Read', 'file_path', file)).join('') + `tool_call('Skill', {'skill': 'lint', 'args': ${JSON.stringify(PLANTED_INPUT)}}, ${JSON.stringify(PLANTED_OUTPUT)})\n`;

const reviewScript = (): string => {
  const child = CHILD_READS.map((file) => `{'tool': 'read', 'input': {'filePath': os.path.join(_cwd, ${JSON.stringify(file)})}, 'output': ${JSON.stringify(PLANTED_OUTPUT)}}`).join(', ');
  return (
    PRELUDE +
    REVIEW_READS.map((file) => read('read', 'filePath', file)).join('') +
    `tool_call('skill', {'name': 'security-review'}, ${JSON.stringify(PLANTED_OUTPUT)})\n` +
    `tool_call('task', {'subagent_type': 'explorer', 'prompt': ${JSON.stringify(PLANTED_PROMPT)}, 'child_tool_calls': [${child}]}, 'delegated')\n`
  );
};

const runnerNode = (prompt: string, to: string, session?: string) => ({
  executor: 'runner',
  prompt,
  judgement: { prompt: PASS, choices: { pass: { description: 'Committed and green.', to } } },
  retries: { max: 1, exhausted: 'escalate' },
  ...(session === undefined ? {} : { session }),
});

/** `build` on Claude Code, then `review` pinned to OpenCode. */
const readsGraph = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  sessions: { [REVIEW_SESSION]: { harnesses: ['opencode'], model: [REVIEW_MODEL] } },
  nodes: { build: runnerNode(buildScript(), 'review'), review: runnerNode(reviewScript(), 'done', `fresh:${REVIEW_SESSION}`) },
});

const askGraph = JSON.stringify({
  name: 'default-delivery',
  entry: 'clarify',
  nodes: {
    clarify: runnerNode(PRELUDE + ASK_BEFORE.map((file) => read('Read', 'file_path', file)).join('') + `ask(${JSON.stringify(ASK_QUESTION)}, ["rest", "graphql"])\n`, 'done'),
  },
});

const answerScript = (): string => PRELUDE + ASK_AFTER.map((file) => read('Read', 'file_path', file)).join('');

test.describe('at fresh-hub', () => {
  test.use({
    isolation: 'fresh-hub',
    hubConfig: { egress: { directory: '${egress}', settle_seconds: 0n, min_free_bytes: 0n } },
    hubEnv: { BZ_EGRESS_PATH_KEY: PATH_KEY },
    runnerConfig: {
      transcripts: { ship: true },
      runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' } }],
    },
  });

  test('test_the_exported_events_agree_with_the_hubs_own_counts', async ({ api, cli, egress, issue, repository, runnerHost }) => {
    test.setTimeout(300_000);
    const repo = await repository('toy-api');
    // The export's first pass only anchors its cursors, so it runs before any work is done.
    await api.sweep('egress', { allTenants: true });
    const status = async (chunkId: string): Promise<string> => (await api.get(`/api/chunks/${chunkId}`)).body.status as string;
    const chunkIds: string[] = [];
    const ingest = async (graph: string, name: string): Promise<string> => {
      expect((await api.post('/api/graphs', { data: { definition_yaml: graph } })).status).toBe(201);
      const { chunk } = await issue(`EGRESS-EVENTS-${RUN}-${name}`, { repository: repo });
      expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
      chunkIds.push(chunk.chunk_id);
      return chunk.chunk_id;
    };
    const stepTo = (chunkId: string, targets: string[]): Promise<string> => stepUntil(runnerHost, () => status(chunkId), (s) => targets.includes(s), 400);
    const blizzard = async (args: string[]): Promise<string> => {
      const done = await cli(args);
      expect(done.code, `blizzard ${args.join(' ')} failed:\n${done.stdout}\n${done.stderr}`).toBe(0);
      return done.stdout;
    };
    const hubCounts = async (dataset: string): Promise<Record<string, number>> => {
      const counts = JSON.parse(await blizzard(['hub', 'analytics', 'summary', dataset, '--json'])).counts as { key: string; count: number }[];
      return Object.fromEntries(counts.map((row) => [row.key, row.count]));
    };
    // Re-derives every chunk until the hub reports nothing left, at most a fixed number of rounds each.
    const deriveEverything = async (): Promise<void> => {
      for (const chunkId of chunkIds) {
        let remaining = Infinity;
        for (let round = 0; round < 10 && remaining > 0; round++) {
          const derived = await api.post('/api/analytics/re-derive', { data: { chunk_id: chunkId, limit: 200 } });
          expect(derived.status, JSON.stringify(derived.body)).toBe(200);
          remaining = derived.body.remaining as number;
        }
        expect(remaining, `derivation of ${chunkId} never converged`).toBe(0);
      }
    };
    const exported = async (): Promise<void> => {
      await api.sweep('egress', { allTenants: true });
    };
    // A path as the hub's counts hold it is absolute; the export's is relative to the environment the worker ran in.
    const inEnvironment = (file: string): string => {
      const prefix = `${runnerHost.workspace}/`;
      if (!file.startsWith(prefix)) throw new Error(`${file} is under no workspace ${runnerHost.workspace}`);
      return file.slice(prefix.length).split('/').slice(1).join('/');
    };
    const grouped = async (sql: string): Promise<Record<string, number>> =>
      Object.fromEntries((await egress.query('events', sql)).map((row) => [String(row.key), Number(row.n)]));
    /** What differs between the export's `events_current` and the hub's own counts; empty when they agree. */
    const differences = async (): Promise<string[]> => {
      if (!egress.exports('events')) return ['the export holds no events files yet'];
      const found: string[] = [];
      const compare = (label: string, exportedCounts: Record<string, number>, hub: Record<string, number>): void => {
        if (JSON.stringify(Object.entries(exportedCounts).sort()) !== JSON.stringify(Object.entries(hub).sort())) {
          found.push(`${label}: export ${JSON.stringify(exportedCounts)} != hub ${JSON.stringify(hub)}`);
        }
      };
      const hubFiles: Record<string, number> = {};
      for (const [key, count] of Object.entries(await hubCounts('counts-files'))) {
        const file = inEnvironment(key);
        hubFiles[file] = (hubFiles[file] ?? 0) + count;
      }
      compare('files', await grouped("SELECT subject AS key, count(*)::INTEGER AS n FROM events_current WHERE kind = 'file_read' GROUP BY subject"), hubFiles);
      compare('skills', await grouped("SELECT subject AS key, count(*)::INTEGER AS n FROM events_current WHERE kind = 'skill_invocation' GROUP BY subject"), await hubCounts('counts-skills'));
      compare('agent types', await grouped('SELECT agent_type AS key, count(*)::INTEGER AS n FROM events_current WHERE agent_type IS NOT NULL GROUP BY agent_type'), await hubCounts('counts-agent-types'));
      compare('nodes', await grouped('SELECT node_id AS key, count(*)::INTEGER AS n FROM events_current GROUP BY node_id'), await hubCounts('counts-nodes'));
      return found;
    };
    const agreeing = async (): Promise<string[]> => {
      await exported();
      return differences();
    };

    const reads = await ingest(readsGraph, 'reads');
    expect(await stepTo(reads, ['done', 'stopped', 'needs_human'])).toBe('done');

    const ask = await ingest(askGraph, 'ask');
    expect(await stepTo(ask, ['waiting_on_human', 'done', 'stopped', 'needs_human'])).toBe('waiting_on_human');
    const question = ((await api.get(`/api/chunks/${ask}`)).body.questions as { question_id: string }[])[0]!.question_id;
    await blizzard(['hub', 'question', 'answer', question, answerScript(), '--by', 'events-resolver']);
    expect(await stepTo(ask, ['done', 'stopped', 'needs_human'])).toBe('done');

    await deriveEverything();

    // 1. The export's current events equal the hub's own counts.
    await expect.poll(agreeing, { message: 'the export to agree with the hub’s counts', timeout: 120_000 }).toEqual([]);
    const before = await egress.query('events', 'SELECT count(*)::INTEGER AS n FROM events_current');
    expect(Number(before[0]!.n)).toBeGreaterThan(0);

    // 2. The docs' recipe names the files the review station read, the child's included.
    const files = await egress.recipe('files-a-station-read-last-week');
    const readByReview = files.filter((row) => row.node_name === 'review').map((row) => row.path as string).sort();
    expect(readByReview).toEqual([...REVIEW_READS, ...CHILD_READS].sort());

    // 3. Nothing the transcripts carried but the event itself leaves.
    const planted = { 'tool input': PLANTED_INPUT, 'tool output': PLANTED_OUTPUT, prompt: PLANTED_PROMPT, 'path key': PATH_KEY, 'ask question': ASK_QUESTION };
    const documents = egress.documents();
    const texts = [...(await egress.rowTexts('events')), ...documents.manifests, ...documents.schemas];
    expect(texts.length).toBeGreaterThan(0);
    for (const [kind, value] of Object.entries(planted)) {
      expect(texts.some((text) => text.includes(value)), `the export carries the planted ${kind}`).toBe(false);
    }
    expect(texts.some((text) => text.includes('"payload"'))).toBe(false);
    expect(texts.some((text) => text.includes(runnerHost.workspace)), 'a working directory left').toBe(false);

    // 4. A forced re-derivation leaves the counts where they were.
    const segments = (await api.get(`/api/chunks/${reads}/transcripts`)).body.segments as { segment_id: string }[];
    expect(segments.length, 'the reads chunk shipped no transcript').toBeGreaterThan(0);
    const segmentId = segments[0]!.segment_id;
    await blizzard(['hub', 'analytics', 're-derive', '--segment', segmentId]);
    const recordsOf = async (recordType: string): Promise<number> => {
      await exported();
      if (!egress.exports('events')) return 0;
      const rows = await egress.query('events', `SELECT count(*)::INTEGER AS n FROM events WHERE record_type = '${recordType}' AND segment_id = '${segmentId}'`);
      return Number(rows[0]!.n);
    };
    await expect.poll(() => recordsOf('derivation'), { message: 'the re-derivation to be exported', timeout: 120_000 }).toBeGreaterThanOrEqual(2);
    expect(await differences()).toEqual([]);

    // 5. A reship supersedes the segment: the export drops it, derives its successor, and the counts hold.
    // The verb works on the host's directory, which a running host holds.
    await runnerHost.stop();
    await blizzard(['runner', 'transcript', 'reship', segmentId, '--dir', runnerHost.dir]);
    await runnerHost.restart();
    await expect.poll(async () => {
      expect((await runnerHost.stepHost()).outcome).toBe('completed');
      expect((await runnerHost.step()).outcome).toBe('completed');
      await api.sweep('event_derivation');
      await deriveEverything();
      return recordsOf('dropped');
    }, { message: 'the superseded segment to be dropped in the export', timeout: 90_000 }).toBeGreaterThanOrEqual(1);
    await deriveEverything();
    await expect.poll(agreeing, { message: 'the counts to agree after the reship', timeout: 120_000 }).toEqual([]);
  });
});
