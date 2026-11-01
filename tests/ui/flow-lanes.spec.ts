import { expect, stepUntil, test, type Wire } from '../harness/fixtures';

test.use({ isolation: 'fresh-tenant' });

test('test_the_delivery_band_shows_a_branch_line_and_collapses_live_once_it_lands', async ({ api, forge, issue, repository, runnerHosts, page, wideViewport }) => {
  test.setTimeout(240_000);
  await page.setViewportSize(wideViewport);
  const repo = await repository();
  const name = `${repo.owner}/${repo.name}`;
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  expect((await forge.call('POST', '/_levers/checks_pending', { repo: name })).status).toBe(200);
  const ids: string[] = [];
  for (const file of ['FIRST.md', 'SECOND.md']) {
    const branch = `feature/${file.toLowerCase().replace('.md', '')}`;
    const prompt = [
      'import os, pathlib, subprocess',
      `path = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / ${JSON.stringify(repo.name)}`,
      `subprocess.run(['git', '-C', str(path), 'switch', '-C', ${JSON.stringify(branch)}], check=True)`,
      `(path / ${JSON.stringify(file)}).write_text('held on a flow\\n')`,
      "subprocess.run(['git', '-C', str(path), 'add', '-A'], check=True)",
      "subprocess.run(['git', '-C', str(path), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: flow'], check=True)",
      "sha = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
      `subprocess.run(['git', '-C', str(path), 'push', '--force', 'origin', ${JSON.stringify(branch)}], check=True)`,
      `subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', ${JSON.stringify(repo.name)}, '--branch', ${JSON.stringify(branch)}, '--commit', sha], check=True)`,
    ].join('\n');
    const definition = { name: 'default-delivery', entry: 'build', nodes: {
      build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
        landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
        failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base failure.', to: 'build' },
      } } },
    } };
    expect((await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(definition) } })).status).toBe(201);
    const id = (await issue(file, { repository: repo })).chunk.chunk_id;
    ids.push(id);
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    const detail = await stepUntil(host, async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body,
      (row) => row.pending !== null || ['done', 'needs_human', 'stopped'].includes(row.status), 80);
    expect(detail.pending, JSON.stringify(detail)).not.toBeNull();
  }
  const response = await api.get<{ flows: { flow_id: string; repository_name: string | null; repository_slug: string; branch: string; line: { chunk_id: string; arrival: number }[] }[] }>('/api/flows?active=true');
  expect(response.status).toBe(200);
  expect(response.body.flows).toHaveLength(1);
  const flow = response.body.flows[0]!;
  expect(flow.line.slice().sort((a, b) => a.arrival - b.arrival).map((place) => place.chunk_id)).toEqual(ids);
  const path = `${flow.repository_name || flow.repository_slug}/${flow.branch}`;
  await page.goto('/board');
  const lane = page.locator(`[data-testid="delivery-lane"][data-flow="${flow.flow_id}"]`);
  await expect(page.getByTestId('delivery-lane')).toHaveCount(1);
  await expect(lane.getByTestId('delivery-lane-title')).toContainText(flow.branch);
  const standing = lane.locator('[data-testid="delivery-landing"], [data-testid="delivery-in-line"]');
  await expect(standing).toHaveCount(2);
  await expect(standing.nth(0)).toHaveAttribute('data-chunk', ids[0]!);
  await expect(standing.nth(1)).toHaveAttribute('data-chunk', ids[1]!);
  expect(await lane.getByTestId('delivery-landing').count()).toBeLessThanOrEqual(1);
  await expect(lane).not.toHaveClass(/\bcollapsed\b/);
  for (const [index, id] of ids.entries()) {
    const place = page.locator(`[data-testid="chunk-card"][data-chunk="${id}"]`).getByTestId('card-flow-place');
    const text = (await place.innerText()).trim();
    expect(index === 0 ? [`landing on ${path}`, `1st on ${path}`] : [`landing on ${path}`, `1st on ${path}`, `2nd on ${path}`]).toContain(text);
  }
  expect((await forge.call('DELETE', `/_levers/checks_pending?repo=${encodeURIComponent(name)}`)).status).toBe(200);
  await expect(lane).toHaveClass(/\bcollapsed\b/, { timeout: 60_000 });
  await expect(standing).toHaveCount(0);
  await expect(lane.getByTestId('delivery-verdict')).toContainText('landed');
  await expect(page.getByTestId('card-flow-place')).toHaveCount(0);
  for (const id of ids) {
    const detail = (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body;
    expect([detail.status, detail.landed]).toEqual(['done', true]);
  }
  await lane.getByTestId('delivery-lane-title').click();
  await expect(page).toHaveURL(new RegExp(`[?&]flow=${flow.flow_id}(&|$)`));
  const record = page.getByTestId('flow-record');
  await expect(record).toBeVisible();
  const landings = record.locator('[data-testid="flow-record-fact"][data-kind="landed"]');
  await expect(landings).toHaveCount(2);
  expect((await Promise.all([0, 1].map((n) => landings.nth(n).getByTestId('flow-record-chunk').getAttribute('data-chunk')))).sort()).toEqual(ids.slice().sort());
});
