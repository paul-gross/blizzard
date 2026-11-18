import { expect, test } from '../harness/fixtures';

// Host links select the first same-hostname HTTP origin, without probing the declared URLs.
test.use({ isolation: 'fresh-tenant' });

test('host detail links the first matching hostname without health probes', async ({ page, api, runnerHost, wideViewport }) => {
  test.setTimeout(120_000);
  await page.goto('/fleet');
  const matching = `http://${new URL(page.url()).hostname}:1`;
  const other = 'http://example.invalid:1';
  await runnerHost.restart();
  await runnerHost.stop();
  await runnerHost.reconfigure({ public_url: [other, matching, runnerHost.url] });
  await runnerHost.restart();
  await runnerHost.stepHost();

  const hosts = await api.get('/api/hosts');
  expect(hosts.status).toBe(200);
  const [host] = hosts.body.hosts as { host_id: string; urls: string[] }[];
  expect(host!.urls).toEqual([other, matching, runnerHost.url]);

  await page.setViewportSize(wideViewport);
  const probes: string[] = [];
  page.on('request', (request) => {
    if ([other, matching, runnerHost.url].some((origin) => request.url().startsWith(`${origin}/api/health`))) probes.push(request.url());
  });
  await page.goto(`/fleet/${host!.host_id}`);
  const link = page.getByTestId('fleet-fact-url').getByTestId('host-origin-link');
  await expect(link).toHaveAttribute('href', matching);
  await expect(link).toHaveText(matching);
  expect(probes).toEqual([]);
});
