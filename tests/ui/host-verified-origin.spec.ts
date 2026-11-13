import { expect, test } from '../harness/fixtures';

// The host detail links the first declared origin that answers /api/health as that host. A real runner host whose
// hub_url is this test's hub declares an origin nothing answers on, then its own: the board, a page on the hub's
// origin, probes both cross-origin, so the link to the second proves the runner admitted the hub's origin end to end.
// It enrolls a runner host, so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

// Port 1 refuses a connection on loopback, so the probe settles as unreachable at once.
const UNANSWERING = 'http://127.0.0.1:1';

test('test_host_detail_links_the_first_origin_that_verifies_as_the_host', async ({ page, api, runnerHost, wideViewport }) => {
  test.setTimeout(120_000);
  // The first boot publishes the host's URL; the restart pinned to it declares both origins, the dead one first.
  await runnerHost.restart();
  await runnerHost.stop();
  runnerHost.reconfigure({ public_url: [UNANSWERING, runnerHost.url] });
  await runnerHost.restart();
  await runnerHost.stepHost();

  const hosts = await api.get('/api/hosts');
  expect(hosts.status).toBe(200);
  const [host] = hosts.body.hosts as { host_id: string; urls: string[] }[];
  expect(host!.urls).toEqual([UNANSWERING, runnerHost.url]);

  await page.setViewportSize(wideViewport);
  await page.goto(`/fleet/${host!.host_id}`);
  const link = page.getByTestId('fleet-fact-url').getByTestId('host-origin-link');
  await expect(link).toHaveAttribute('href', runnerHost.url);
  await expect(link).toHaveText(runnerHost.url);
});
