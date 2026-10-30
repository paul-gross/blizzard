import { expect, stepUntil, test } from '../harness/fixtures';

// The mission-control board driven end to end over a stepped runner host: promote from the board, a grouping landing
// live, a drag-reorder the claim honours, a parked question answered from the dock, the hub brake set and released from
// the Fleet tab, and a chunk paused and resumed from the dock, the board loaded once throughout. The graph ends at
// review rather than delivering: the host's origins are not the hub forge's repositories, so a pushed branch reaches
// the host's origin and is read back there, and landing through the forge is not proven here. The dock's geometry is
// `web:shell-sweep`'s. It enrolls a host and reshapes the tenant's queue, so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

const REPO = 'toy-api';
const BRANCH = 'feature/scripted-build';
const REPO_PATH = `str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${REPO}')`;

// build's first turn asks an undecidable question and exits; a person answers it from the board.
const ASK_SCRIPT = 'ask("Which API style should the grouped endpoint use?", ["rest", "graphql"])\n';

/**
 * The resumed build, as the answer: commits LANDED.md on the build branch, pushes it and declares it. The board's answer
 * field is single-line, so the program is one line of semicolon-separated Python.
 */
function answerScript(cli: string): string {
  const git = (...args: string[]) => `subprocess.run(["git", "-C", repo_path, ${args.join(', ')}], check=True)`;
  const read = (what: string) => `subprocess.run(["git", "-C", repo_path, "rev-parse", ${what}], check=True, capture_output=True, text=True).stdout.strip()`;
  return [
    'import os, pathlib, subprocess',
    `repo_path = ${REPO_PATH}`,
    `subprocess.run(["git", "-C", repo_path, "switch", "-C", "${BRANCH}"], check=True)`,
    '(pathlib.Path(repo_path) / "LANDED.md").write_text("landed after the board answer\\n")',
    git('"add"', '"-A"'),
    git('"-c"', '"user.email=mock@blizzard.local"', '"-c"', '"user.name=Mock Harness"', '"commit"', '"-m"', '"feat: resolve the board answer and land the change"'),
    `_branch = ${read('"--abbrev-ref", "HEAD"')}`,
    `_commit = ${read('"HEAD"')}`,
    git('"push"', '"origin"', '_branch'),
    `subprocess.run([${JSON.stringify(cli)}, "runner", "artifact", "commit", "--repo", "${REPO}", "--branch", _branch, "--commit", _commit], check=True)`,
  ].join('; ');
}

// build parks on its question; review produces a findings asset for the dock to render and closes the chunk.
const GRAPH = JSON.stringify({
  name: 'default-delivery',
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      prompt: ASK_SCRIPT,
      judgement: {
        prompt: "verdict('pass', 'resumed with the board answer; committed and green')\n",
        choices: { pass: { description: 'Committed and green.', to: 'review' } },
      },
      retries: { max: 1, exhausted: 'escalate' },
    },
    review: {
      executor: 'runner',
      prompt: 'pass\n',
      session: 'fresh',
      produces: ['review-findings'],
      judgement: {
        prompt: "verdict('pass', 'cold-eyes review: clean; ready to deliver')\n",
        choices: { pass: { description: 'Passes cold-eyes review.', to: 'done' }, fail: { description: 'Blocking issues.', to: 'build' } },
      },
      retries: { max: 1, exhausted: 'escalate' },
    },
  },
});

test('test_board_browser_live_group_reorder_answer_and_pause', async ({ api, issue, repository, runnerHost, page, wideViewport }) => {
  test.setTimeout(300000);
  expect((await api.post('/api/graphs', { data: { definition_yaml: GRAPH } })).status).toBe(201);
  const repo = await repository(REPO);
  const ingest = async (title: string) => (await issue(title, { repository: repo })).chunk.chunk_id;
  // A stays behind for the pause proof; B and C are grouped.
  const chunkA = await ingest('chunk A — pause proof');
  const chunkB = await ingest('chunk B — group survivor');
  const chunkC = await ingest('chunk C — group merged');
  const status = async (chunkId: string): Promise<string> => (await api.get(`/api/chunks/${chunkId}`)).body.status;

  // The board loads once and is never reloaded until its own URL contract is checked below.
  await page.setViewportSize(wideViewport);
  await page.goto('/');
  await expect(page.getByTestId('board-shell')).toBeVisible();

  // The runner brake lives on the Fleet tab; a second page drives it, so the board page never navigates.
  const fleetPage = await page.context().newPage();
  await fleetPage.setViewportSize(wideViewport);
  const fleetBrake = async () => {
    const runnerId = await page.getByTestId('runner').getAttribute('data-runner');
    await fleetPage.goto('/fleet');
    // Until the hosts read lands every runner reads as standing under no host, so the host's card settles the list.
    await fleetPage.locator('[data-testid^="fleet-card-hs_"]').first().click();
    await fleetPage.locator(`[data-testid="fleet-runner"][data-runner="${runnerId}"]`).getByTestId('fleet-runner-toggle').click();
  };

  const col = (key: string) => page.locator(`[data-col="${key}"]`);
  const colCards = (key: string) => col(key).getByTestId('chunk-card');
  // Same-instant ids share a prefix, so a card is found by its lane and full id.
  const readyCard = (chunkId: string) => col('ready').locator(`[data-chunk="${chunkId}"]`);

  // All three rest not-ready in the backlog.
  await expect(page.getByTestId('chunk-card')).toHaveCount(3);
  await expect(colCards('notready')).toHaveCount(3);
  await expect(colCards('ready')).toHaveCount(0);
  const backlogGrips = col('notready').getByTestId('board-reorder-grip');
  await expect(backlogGrips).toHaveCount(3);
  await expect(backlogGrips.first()).toHaveAttribute('aria-hidden', 'true');
  for (const retired of ['queue-select', 'group-selected', 'queue-move-top', 'backlog-move-top']) await expect(page.getByTestId(retired)).toHaveCount(0);

  // Titles land from a later read than the cards, and Promote renders once permissions are read: settle both first.
  await expect(col('notready').getByTestId('chunk-title-skeleton')).toHaveCount(0);
  await expect(col('notready').getByTestId('chunk-title')).toHaveCount(3);
  await expect(col('notready').getByTestId('promote-chunk')).toHaveCount(3);

  // Promote order is queue order, so each chunk is promoted by id.
  for (const [promoted, chunkId] of [chunkA, chunkB, chunkC].entries()) {
    await col('notready').locator(`[data-chunk="${chunkId}"]`).getByTestId('promote-chunk').click();
    await expect(colCards('notready')).toHaveCount(2 - promoted);
    await expect(colCards('ready')).toHaveCount(promoted + 1);
  }
  const readyGrips = col('ready').getByTestId('board-reorder-grip');
  await expect(readyGrips).toHaveCount(3);
  await expect(readyGrips.first()).toHaveAttribute('aria-hidden', 'true');

  // Group B and C through the hub API, B the survivor: C vanishes live and B carries both pointers' chips.
  const grouped = await api.post(`/api/chunks/${chunkB}/group`, { data: { merge_chunk_ids: [chunkC] } });
  expect(grouped.status).toBe(200);
  expect(grouped.body.merged_chunk_ids).toEqual([chunkC]);
  await expect(colCards('ready')).toHaveCount(2);
  await expect(readyCard(chunkC)).toHaveCount(0);
  const labels = ((await api.get(`/api/chunks/${chunkB}`)).body.work_refs as { label?: string }[]).map((ref) => ref.label).filter(Boolean).sort();
  const chips = readyCard(chunkB).getByTestId('work-ref-chip');
  await expect(chips).toHaveCount(2);
  expect((await chips.allTextContents()).sort()).toEqual(labels);

  // A leads; B is dragged over it with real pointer moves, which the drop list needs: a short move arms the drag, a long
  // one carries it over A's upper edge, and a last one settles it so the sort has a frame to run.
  await expect(colCards('ready').first()).toHaveAttribute('data-chunk', chunkA);
  const source = (await readyCard(chunkB).boundingBox())!;
  const target = (await readyCard(chunkA).boundingBox())!;
  const x = source.x + source.width / 2;
  await page.mouse.move(x, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, source.y + source.height / 2 - 12, { steps: 4 });
  await page.mouse.move(x, target.y + 2, { steps: 25 });
  await page.mouse.move(x, target.y + 1, { steps: 2 });
  await page.mouse.up();
  await expect(colCards('ready').first()).toHaveAttribute('data-chunk', chunkB);

  // The hub's own record corroborates both shaping actions before the claim.
  expect((await api.get(`/api/chunks/${chunkB}`)).body.work_refs).toHaveLength(2);
  expect((await api.get('/api/queue')).body.entries.map((entry: { chunk_id: string }) => entry.chunk_id)).toEqual([chunkB, chunkA]);

  // The claim honours both: the grouped survivor goes first, and A is untouched.
  await stepUntil(runnerHost, () => status(chunkB), (s) => s === 'running' || s === 'waiting_on_human');
  expect(await status(chunkA)).toBe('ready');
  expect((await api.get(`/api/chunks/${chunkB}`)).body.work_refs).toHaveLength(2);
  await expect(page.getByTestId('runner')).toHaveAttribute('data-online', 'true');

  // B parks on its question and crosses to the waiting lane live; A stays ready.
  expect(await stepUntil(runnerHost, () => status(chunkB), (s) => ['waiting_on_human', 'done', 'needs_human'].includes(s))).toBe('waiting_on_human');
  await expect(colCards('waiting')).toHaveCount(1);
  await expect(col('waiting').getByTestId('chunk-status')).toHaveText('waiting_on_human');
  await expect(readyCard(chunkA)).toHaveCount(1);

  // With nothing selected the dock is closed; selecting B opens it on B's question, answered from the dock.
  const dock = page.locator('app-chunk-detail.dock');
  await expect(dock).toHaveClass(/\bclosed\b/);
  await colCards('waiting').first().click();
  await expect(page.getByTestId('chunk-detail')).toBeVisible();
  await expect(page.getByTestId('detail-id')).toHaveText(chunkB);
  await expect(page.getByTestId('question-text')).toContainText('API style');
  await page.getByTestId('answer-input').fill(answerScript(runnerHost.cliBinary));
  await page.getByTestId('answer-submit').click();
  // The answer closes the open question at the hub, as `hub answer` would.
  await stepUntil(runnerHost, async () => (await api.get('/api/questions')).body, (questions) => questions.length === 0);

  // The hub's brake, not the runner's own: B's finishing frees the runner's only slot, and the brake keeps A unclaimed.
  await fleetBrake();
  await expect(page.getByTestId('runner')).toHaveAttribute('data-hub-paused', 'true');
  await expect(page.getByTestId('runner-hub-paused')).toBeVisible();
  await expect(page.getByTestId('runner-locally-paused')).toHaveCount(0);

  // B runs on to done; its card's node slot is the one place "done" renders.
  expect(await stepUntil(runnerHost, () => status(chunkB), (s) => ['done', 'needs_human', 'stopped'].includes(s))).toBe('done');
  await expect(colCards('done')).toHaveCount(1);
  await expect(col('done').getByTestId('chunk-node')).toHaveText('done');
  await expect(col('done').getByTestId('chunk-status')).toHaveCount(0);

  // The dock, still on B, renders its node history and artifacts, each artifact a link.
  await expect(page.getByTestId('detail-status')).toHaveText('done');
  await expect(page.getByTestId('history-step').first()).toBeVisible();
  await expect(page.getByTestId('artifact').first()).toBeVisible();
  await expect(page.getByTestId('artifact-link').getByTestId('artifact-ref').first()).toBeVisible();

  // A dock link opens the chunk page's Artifacts tab with that artifact selected.
  const firstLink = page.getByTestId('artifact-link').first();
  const targetKey = (await firstLink.getAttribute('data-artifact-key'))!;
  expect(targetKey).toBeTruthy();
  await firstLink.click();
  await expect(page).toHaveURL(`/board/chunk/${chunkB}?tab=artifacts&artifact=${targetKey}`);
  await expect(page.getByTestId('tab-artifacts')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator(`[data-artifact-key="${targetKey}"]`).getByTestId('artifacts-tab-nav-item')).toHaveClass(/\bselected\b/);
  const artifact = ((await api.get(`/api/chunks/${chunkB}`)).body.artifacts as { key: string; kind: string; content: string; commit_hash: string }[]).find((a) => a.key === targetKey)!;
  const viewer = page.getByTestId('artifacts-tab-artifact');
  await expect(viewer).toBeVisible();
  await expect(viewer).toContainText(artifact.kind === 'asset' ? artifact.content.slice(0, 40) : artifact.commit_hash);

  // The breadcrumb navigates back client-side with B re-selected; a fresh mount of the same URL does too.
  await page.getByTestId('mobile-chunk-back').click();
  await expect(page).toHaveURL(`/board?chunk=${chunkB}`);
  await expect(page.getByTestId('chunk-detail')).toBeVisible();
  await page.goto(`/board?chunk=${chunkB}`);
  await expect(page.getByTestId('chunk-detail')).toBeVisible();
  await expect(page.getByTestId('detail-status')).toHaveText('done');
  await page.getByTestId('detail-close').click();
  await expect(dock).toHaveClass(/\bclosed\b/);

  // The brake holds: A alone remains ready through four passes of the host.
  await expect(colCards('ready')).toHaveCount(1);
  await expect(page.getByTestId('runner')).toHaveAttribute('data-hub-paused', 'true');
  for (let pass = 0; pass < 4; pass++) expect((await runnerHost.step()).outcome).toBe('completed');
  expect(await status(chunkA)).toBe('ready');
  await expect(readyCard(chunkA)).toHaveCount(1);

  // Released from the Fleet tab, the claim resumes and A leaves the ready lane.
  await fleetBrake();
  await expect(page.getByTestId('runner')).toHaveAttribute('data-hub-paused', 'false');
  expect(await stepUntil(runnerHost, () => status(chunkA), (s) => s === 'running')).toBe('running');
  await expect(readyCard(chunkA)).toHaveCount(0);

  // A, caught running, is paused from the dock's actions menu; the retired dependency controls stay gone.
  const aCard = page.locator('[data-testid="chunk-card"][data-status="running"]');
  await expect(aCard).toHaveCount(1);
  await aCard.click();
  await expect(page.getByTestId('detail-id')).toHaveText(chunkA);
  for (const retired of ['dependency-prerequisite-input', 'declare-dependency', 'release-dependency']) await expect(page.getByTestId(retired)).toHaveCount(0);
  await page.getByTestId('chunk-actions-menu').click();
  await page.getByTestId('pause-chunk').click();
  await page.getByTestId('confirm-dialog-confirm').click();

  // The chip flips to paused live and the card moves to the waiting lane, keeping its claim.
  await expect(colCards('running')).toHaveCount(0);
  await expect(colCards('waiting')).toHaveCount(1);
  await expect(col('waiting').getByTestId('chunk-status')).toHaveText('paused');
  await expect(readyCard(chunkA)).toHaveCount(0);

  // The runner parks the lease on its next passes, keeping the claim: no requeue, no released route.
  for (let pass = 0; pass < 2; pass++) expect((await runnerHost.step()).outcome).toBe('completed');
  const paused = (await api.get(`/api/chunks/${chunkA}`)).body;
  expect(paused.status).toBe('paused');
  expect(paused.route).not.toBeNull();
  await expect(page.getByTestId('detail-id')).toHaveText(chunkA);
  await expect(page.getByTestId('chunk-pause-by')).toContainText('operator');

  // Resumed from the dock, A proceeds again.
  await page.getByTestId('chunk-actions-menu').click();
  await expect(page.getByTestId('resume-chunk')).toBeVisible();
  await expect(page.getByTestId('pause-chunk')).toHaveCount(0);
  await page.getByTestId('resume-chunk').click();
  await page.getByTestId('confirm-dialog-confirm').click();
  await expect(page.getByTestId('chunk-pause-by')).toHaveCount(0);
  for (let pass = 0; pass < 3; pass++) expect((await runnerHost.step()).outcome).toBe('completed');
  expect(['running', 'waiting_on_human']).toContain(await status(chunkA));
  await expect(page.getByTestId('chunk-card')).toHaveCount(2);

  // The board-answered change reached the host's origin on the build branch.
  const head = await runnerHost.originHead(REPO, BRANCH);
  expect(head).toBeDefined();
  expect(await runnerHost.originTree(REPO, BRANCH)).toContain('LANDED.md');
  expect(await runnerHost.originFile(REPO, BRANCH, 'LANDED.md')).toBe('landed after the board answer\n');
  const log = (await runnerHost.originLog(REPO, BRANCH, 'LANDED.md'))!;
  expect(log[0]).toEqual({ sha: head, subject: 'feat: resolve the board answer and land the change' });
});
