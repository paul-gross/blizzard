import { expect, test } from '../harness/fixtures';

// The acceptance journey as one rehearsal: five issues across two repositories on the mock forge, a night of autonomous
// work (a grouped multi-repo delivery, a review that fails once and then lands, an ask that parks, a genuine
// escalation), a SIGKILL of both daemons mid-run, and the morning after. The runner loop is timed, as an operator's is,
// and every wait is on a chunk's latched state, never on the clock. The graph is the shipped `build → review → deliver`
// with the shipped `land` action; each worker reads its behavior from a directive in its issue body, through
// `blizzard runner work-items`, as the only way a worker learns its work.

const API_REPO = 'toy-api';
const WEB_REPO = 'toy-web';
const OWNER = 'blizzard';
const RUNNER = 'runner-local';
const WORKSPACE = 'workspace-local';
// Parked and escalated chunks keep their environment for resume and takeover, so the pool is wider than the agents.
const MAX_ENVIRONMENTS = 4n;
const MAX_AGENTS = 2n;
const NIGHT_MS = 300_000;

const repoUrl = (name: string): string => `file://\${forgeOrigins}/${OWNER}/${name}.git`;

/** The worker's side of the common steps: commit, push the branch, and declare it through the real verb. */
const helpers = (cli: string): string => String.raw`
import os, json, subprocess, pathlib


def _commit(repo, msg):
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", repo,
         "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",
         "commit", "-m", msg],
        check=True,
    )


def _push_and_declare(repo):
    branch = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    commit_sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", repo, "push", "origin", branch], check=True)
    subprocess.run(
        [${JSON.stringify(cli)}, "runner", "artifact", "commit",
         "--repo", os.path.basename(repo), "--branch", branch, "--commit", commit_sha],
        check=True,
    )
`;

const buildPrompt = (cli: string): string =>
  helpers(cli) +
  String.raw`

chunk_id = os.environ["BLIZZARD_CHUNK_ID"]
# The worker reads its issue only through the hub pass-through.
_raw = subprocess.run(
    [${JSON.stringify(cli)}, "runner", "work-items", chunk_id], check=True, capture_output=True, text=True
).stdout
_body = json.loads(_raw)["items"][0]["body"]


def _field(key, default=""):
    for line in _body.splitlines():
        s = line.strip()
        if s.startswith(key + "="):
            return s[len(key) + 1:].strip()
    return default


behavior = _field("BEHAVIOR", "clean")
_workdir = pathlib.Path(os.environ["BLIZZARD_ENV_WORKDIRS"].split(",")[0])
repos = [str(_workdir / r.strip()) for r in _field("REPOS", ${JSON.stringify(API_REPO)}).split(",") if r.strip()]
_BRANCH = "feature/" + chunk_id


def _switch(repo):
    # A worktree arrives detached at origin/<base>; work on a branch, kept across re-builds.
    subprocess.run(["git", "-C", repo, "switch", "-C", _BRANCH], check=True, capture_output=True)


fname = _field("FILE", "LANDED.md")
pathlib.Path(".behavior").write_text(behavior)

if behavior == "ask":
    if answer() is None:
        ask("Which API style should the endpoint use?", ["rest", "graphql"])
    # On resume the human's answer script (not this prompt) runs and makes the commit.
elif behavior == "escalate":
    pass  # no work; the judgement below emits no verdict, so every attempt fails
else:  # clean / review-fail: a real change in each repo (append so a re-build commits again)
    for repo in repos:
        _switch(repo)
        p = pathlib.Path(repo) / fname
        p.write_text((p.read_text() if p.exists() else "") + "landed " + behavior + "\n")
        _commit(repo, "feat: " + behavior + " land in " + repo)
        _push_and_declare(repo)
`;

/** The fail edge's addendum, run on build's re-entry: its marker reaches `main` only if the findings threaded back. */
const REVIEW_ADDENDUM = String.raw`
for repo in repos:
    pathlib.Path(repo, "REVIEW_ADDRESSED.md").write_text("addressed the review findings\n")
    _commit(repo, "fix: address review findings")
    _push_and_declare(repo)
`;

const BUILD_JUDGEMENT = String.raw`
import pathlib
b = pathlib.Path(".behavior").read_text().strip() if pathlib.Path(".behavior").exists() else "clean"
if b == "escalate":
    pass  # no verdict: a failed attempt
else:
    verdict("pass", "build checks are green")
`;

const REVIEW_JUDGEMENT = String.raw`
import pathlib
b = pathlib.Path(".behavior").read_text().strip() if pathlib.Path(".behavior").exists() else "clean"
if b == "review-fail":
    m = pathlib.Path(".review-count")
    n = (int(m.read_text()) if m.exists() else 0) + 1
    m.write_text(str(n))
    if n == 1:
        verdict("fail", "BLOCKING: guard the empty input before delivery")
    else:
        verdict("pass", "findings addressed; ready to deliver")
else:
    verdict("pass", "cold-eyes review: clean; ready to deliver")
`;

/** The answer the operator types at the hub: the parked session execs it as its resume message, and it lands the asked chunk's change. */
const answerScript = (cli: string): string =>
  helpers(cli) +
  String.raw`
repo = str(pathlib.Path(os.environ["BLIZZARD_ENV_WORKDIRS"].split(",")[0]) / ${JSON.stringify(WEB_REPO)})
subprocess.run(["git", "-C", repo, "switch", "-C", "feature/ask"], check=True)
(pathlib.Path(repo) / "LANDED-ask.md").write_text("landed after the human answered\n")
_commit(repo, "feat: resolve the ask and land")
_push_and_declare(repo)
`;

const graphDefinition = (cli: string): string =>
  JSON.stringify({
    name: 'default-delivery',
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        prompt: buildPrompt(cli),
        judgement: { prompt: BUILD_JUDGEMENT, choices: { pass: { description: 'Committed and green.', to: 'review' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
      review: {
        executor: 'runner',
        prompt: 'pass\n',
        session: 'fresh',
        produces: ['review-findings'],
        judgement: {
          prompt: REVIEW_JUDGEMENT,
          choices: {
            pass: { description: 'Passes cold-eyes review.', to: 'deliver' },
            fail: { description: 'Blocking issues found.', to: 'build', prompt_addendum: REVIEW_ADDENDUM },
          },
        },
        retries: { max: 1, exhausted: 'escalate' },
      },
      deliver: {
        executor: 'hub',
        action: 'land',
        poll_interval: 1,
        judgement: {
          choices: {
            landed: { description: 'Every repo merged cleanly into its base branch.', to: 'done' },
            conflict: { description: 'A repo did not merge cleanly; kicked back to build.', to: 'build' },
            failure: { description: 'A repo failed to land; kicked back to build.', to: 'build' },
            'inherited-failure': { description: 'The base check stays red; back to build.', to: 'build' },
          },
        },
      },
    },
  });

const issueBody = (behavior: string, repos: string, file: string): string => `BEHAVIOR=${behavior}\nREPOS=${repos}\nFILE=${file}\n`;

/** The parts of a chunk's detail the journey reads. */
interface ChunkDetail {
  status: string;
  graph_id: string;
  history: { choice_name: string }[];
  artifacts: { kind: string; name: string; node_name: string; repo?: string; content?: string }[];
  questions: { question_id: string; session_id: string }[];
  escalation: { takeover_command: string; wrapped_takeover_command: string };
}

/** `shlex.quote`: a word the shell reads back as itself. */
const quote = (word: string): string => (/^[\w@%+=:,./-]+$/.test(word) ? word : `'${word.replace(/'/g, `'"'"'`)}'`);

// The timeout covers the whole night, fixtures' setup included.
test.setTimeout(900_000);

test.use({
  isolation: 'fresh-hub',
  runnerLoop: 'timed',
  // The night begins when the queue is arranged, so the host is initialized and left unstarted.
  runnerStarted: false,
  runnerEnv: { BZ_RUNNER_TICK_SECONDS: '1' },
  runnerOnForge: [API_REPO, WEB_REPO],
  runnerConfig: {
    workspace: [{ id: WORKSPACE, max_environments: MAX_ENVIRONMENTS, repo: [{ name: API_REPO, url: repoUrl(API_REPO) }, { name: WEB_REPO, url: repoUrl(WEB_REPO) }] }],
    runner: [{ name: RUNNER, agents: MAX_AGENTS }],
  },
});

test('test_the_acceptance_journey_end_to_end', async ({ api, cli, forge, freshHub, project, repository, runnerHost }) => {
  const cliPath = runnerHost.cliBinary;
  const detail = async (chunkId: string): Promise<ChunkDetail> => (await api.get(`/api/chunks/${chunkId}`)).body as unknown as ChunkDetail;
  const reaches = (chunkId: string, status: string, timeout = NIGHT_MS) =>
    expect.poll(async () => (await detail(chunkId)).status, { message: `chunk ${chunkId} to ${status}`, timeout }).toBe(status);
  const blizzard = async (args: string[]): Promise<string> => {
    const done = await cli(args);
    expect(done.code, `blizzard ${args.join(' ')} failed:\n${done.stdout}${done.stderr}`).toBe(0);
    return done.stdout;
  };
  /** A pass the host serves on request, retried while a timed pass holds the lock. */
  const requestedPass = () =>
    expect.poll(async () => {
      try {
        return (await runnerHost.step()).outcome === 'completed';
      } catch (error) {
        if (String(error).includes('tick_in_progress')) return false;
        throw error;
      }
    }, { message: 'a completed requested pass', timeout: 60_000 }).toBe(true);
  /** `blizzard dev check-invariants` over both running daemons, in client mode. */
  const invariantsHold = async (when: string): Promise<void> => {
    const checked = await cli(['dev', 'check-invariants', '--dir', runnerHost.dir]);
    expect(checked.code, `invariant violations ${when}:\n${checked.stdout}${checked.stderr}`).toBe(0);
  };

  const repositories = { [API_REPO]: await repository(API_REPO), [WEB_REPO]: await repository(WEB_REPO) };
  const graph = await api.post('/api/graphs', { data: { definition_yaml: graphDefinition(cliPath) } });
  expect(graph.status, JSON.stringify(graph.body)).toBe(201);

  // -- Five issues across the two repositories, ingested by token --
  const file = async (repo: string, title: string, body: string): Promise<string> => {
    const target = repositories[repo as keyof typeof repositories];
    const issue = await forge.createIssue(`${target.owner}/${target.name}`, { title, body });
    const ingested = await api.post('/api/chunks', { data: { tokens: [`${target.tokenPrefix}:${issue.number}`], project: project.slug } });
    expect(ingested.status, JSON.stringify(ingested.body)).toBe(201);
    const chunkId = ingested.body.chunk_id as string;
    // The graph is reused by name on ingest; a chunk that landed on another copy is moved onto the one minted here.
    if ((await detail(chunkId)).graph_id !== graph.body.graph_id) {
      expect((await api.patch(`/api/chunks/${chunkId}`, { data: { graph_id: graph.body.graph_id } })).status).toBe(202);
    }
    expect((await api.post(`/api/chunks/${chunkId}/promote`)).status).toBe(202);
    return chunkId;
  };
  const groupedApi = await file(API_REPO, 'grouped: pagination', issueBody('clean', `${API_REPO},${WEB_REPO}`, 'LANDED-grouped.md'));
  const groupedWeb = await file(WEB_REPO, 'grouped: pagination UI', issueBody('clean', WEB_REPO, 'LANDED-grouped-2.md'));
  const review = await file(WEB_REPO, 'restyle button', issueBody('review-fail', WEB_REPO, 'BUILD-review.md'));
  const ask = await file(WEB_REPO, 'config parser', issueBody('ask', WEB_REPO, 'LANDED-ask.md'));
  const escalate = await file(API_REPO, 'flaky login', issueBody('escalate', API_REPO, 'LANDED-escalate.md'));

  // The two related ones become one chunk, the survivor's issue body driving it; it is moved to the top of the queue.
  const grouped = await api.post(`/api/chunks/${groupedApi}/group`, { data: { merge_chunk_ids: [groupedWeb] } });
  expect(grouped.status, JSON.stringify(grouped.body)).toBe(200);
  expect(grouped.body.chunk_id).toBe(groupedApi);
  expect(grouped.body.merged_chunk_ids).toContain(groupedWeb);
  const queue = async (): Promise<string[]> => ((await api.get('/api/queue')).body.entries as { chunk_id: string }[]).map((entry) => entry.chunk_id);
  const reordered = await api.put('/api/queue', { data: { chunk_ids: [groupedApi, ...(await queue()).filter((id) => id !== groupedApi)] } });
  expect(reordered.status, JSON.stringify(reordered.body)).toBe(200);
  const queued = await queue();
  expect(queued[0], 'the riskiest chunk is not at the top of the queue').toBe(groupedApi);
  expect(queued, 'the merged-away chunk still shows in the queue').not.toContain(groupedWeb);

  // -- The fleet works overnight; each wait is on a latched state --
  await runnerHost.restart();
  await reaches(groupedApi, 'done');
  await reaches(review, 'done');
  await reaches(ask, 'waiting_on_human');
  await reaches(escalate, 'needs_human');
  const chunks = { grouped: groupedApi, review, ask, escalate };

  // -- The reboot: both daemons killed, restarted on their directories and ports --
  await runnerHost.kill();
  await freshHub.kill();
  await freshHub.start();
  await runnerHost.restart();

  // The reboot changed nothing: the parked lease was not reaped and the terminal chunks stayed terminal.
  expect(await Promise.all(Object.values(chunks).map(async (id) => (await detail(id)).status))).toEqual(['done', 'done', 'waiting_on_human', 'needs_human']);
  await requestedPass();
  await invariantsHold('after the reboot');

  // -- The human loop: the takeover command names the runner, and run verbatim it resumes the stuck session --
  const runnerName = (await runnerHost.api.runners()).body.items[0]!.name;
  const { takeover_command: takeover, wrapped_takeover_command: wrapped } = (await detail(escalate)).escalation;
  expect(takeover, 'no pasteable takeover command on the escalation').toBeTruthy();
  expect(wrapped).toBe(`blizzard runner takeover ${escalate} --dir ${quote(runnerHost.dir)} --runner ${quote(runnerName)}`);

  const resumed = /--resume (\S+)/.exec(takeover);
  expect(resumed, `takeover command carries no --resume session: ${takeover}`).not.toBeNull();
  const escalatedSession = resumed![1]!;
  const sessionOf = async (id: string) => (await runnerHost.mockSessions()).find((record) => record.session_id === id);
  const before = await sessionOf(escalatedSession);
  expect(before, 'the parked session state should exist').toBeDefined();
  const marker = `human-takeover-${Math.random().toString(16).slice(2, 12)}`;
  const resumedRun = await runnerHost.shell(takeover, `# ${marker}\n`);
  expect(resumedRun.code, `takeover command failed:\n${resumedRun.stdout}${resumedRun.stderr}`).toBe(0);
  const after = await sessionOf(escalatedSession);
  expect(after?.turns, 'the takeover did not advance the session turn').toBe((before!.turns as number) + 1);
  expect((after?.resumes as string[]).some((message) => message.includes(marker)), 'the takeover message is not recorded on resume').toBe(true);

  // The parked chunk is answered at the hub, no takeover, and resumes to done: the open question survived the restart.
  const question = (await detail(ask)).questions[0]!;
  await blizzard(['hub', 'question', 'answer', question.question_id, answerScript(cliPath), '--by', 'alice']);

  // -- The morning after --
  for (const id of [groupedApi, review, ask]) await reaches(id, 'done');
  expect((await detail(escalate)).status).toBe('needs_human');
  await requestedPass();
  await invariantsHold('in the morning');

  const main = 'main';
  const apiRepo = `${OWNER}/${API_REPO}`;
  const webRepo = `${OWNER}/${WEB_REPO}`;
  // The grouped change landed, once, on both repositories.
  for (const repo of [apiRepo, webRepo]) {
    expect(await forge.originTree(repo, main), `grouped change missing from ${repo}`).toContain('LANDED-grouped.md');
    expect((await forge.originLog(repo, main, 'LANDED-grouped.md'))?.length, `LANDED-grouped.md landed more or less than once on ${repo}`).toBe(1);
  }
  // The review chunk built twice, and the fail edge's marker landed: the findings threaded back through the envelope.
  const built = await forge.originFile(webRepo, main, 'BUILD-review.md');
  expect(built?.split('landed review-fail').length, `review-fail build did not run twice:\n${built}`).toBe(3);
  const webTree = await forge.originTree(webRepo, main);
  expect(webTree).toContain('REVIEW_ADDRESSED.md');
  expect(webTree, 'the answered chunk’s change did not land').toContain('LANDED-ask.md');
  // The escalated chunk never delivered.
  expect(await forge.originTree(apiRepo, main)).not.toContain('LANDED-escalate.md');

  // History: the grouped chunk walked its three edges and declared a build commit per repository.
  const groupedDetail = await detail(groupedApi);
  expect(groupedDetail.history, 'the grouped chunk did not walk all three edges').toHaveLength(3);
  expect(groupedDetail.history.map((entry) => entry.choice_name)).toContain('landed');
  const buildRepos = new Set(groupedDetail.artifacts.filter((artifact) => artifact.kind === 'git_commit' && artifact.node_name === 'build').map((artifact) => artifact.repo));
  expect([...buildRepos]).toEqual(expect.arrayContaining([API_REPO, WEB_REPO]));
  const reviewDetail = await detail(review);
  const choices = reviewDetail.history.map((entry) => entry.choice_name);
  expect(choices, 'no review-fail step in the history').toContain('fail');
  expect(choices, 'no passing step in the history').toContain('pass');
  const findings = reviewDetail.artifacts.filter((artifact) => artifact.name === 'review-findings' && artifact.kind === 'asset');
  expect(findings.some((artifact) => (artifact.content ?? '').includes('BLOCKING')), 'the findings asset is not exposed').toBe(true);

  // The ask resumed without a takeover.
  const asked = await sessionOf(question.session_id);
  expect((asked?.resumes as string[]).some((message) => message.includes('resolve the ask')), 'the answer is not recorded on resume').toBe(true);
  expect((asked?.resumes as string[]).some((message) => message.includes('human-takeover')), 'the asked chunk needed a takeover').toBe(false);

  // No environment is orphaned: the escalated chunk holds the only one held.
  const held = (await runnerHost.api.environments()).body.workspaces.flatMap((workspace) => workspace.items).filter((environment) => environment.held);
  expect(held.map((environment) => environment.chunk_id)).toEqual([escalate]);

  // `hub status` tells the truth about every chunk.
  const status = await blizzard(['hub', 'status']);
  const expected: Record<string, string> = { [groupedApi]: 'done', [review]: 'done', [ask]: 'done', [escalate]: 'needs_human' };
  for (const [chunkId, state] of Object.entries(expected)) {
    const line = status.split('\n').find((candidate) => candidate.includes(chunkId));
    expect(line, `hub status omitted chunk ${chunkId}:\n${status}`).toBeDefined();
    expect(line, `hub status lied about ${chunkId}`).toContain(state);
  }
});
