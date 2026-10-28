import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { expect, runnerTest as test } from '../../fixtures';
import { defaultLevels } from '../../vendor-usage';


test.describe('with sampling on', () => {
  test.use({ runnerSampling: true });

  test('a stepHost records the counterpart’s windows, and the next one the new ones with no wait', async ({ runnerHost, vendorUsage }) => {
    // The one credential the fixture minted for the sampling profile, in the host's scratch root.
    const vendorDir = path.join(path.dirname(runnerHost.dir), 'vendor');
    const [credential, ...others] = fs.readdirSync(vendorDir);
    expect(others).toEqual([]);
    const token = (JSON.parse(fs.readFileSync(path.join(vendorDir, credential as string), 'utf8')) as { claudeAiOauth: { accessToken: string } }).claudeAiOauth.accessToken;
    const now = Math.floor(Date.now() / 1000);

    vendorUsage.set(token, { short: { utilization: 41, resetsAt: now + 3_600 }, long: { utilization: 63, resetsAt: now + 86_400 } });
    expect((await runnerHost.stepHost()).outcome).toBe('completed');
    const first = (await runnerHost.api.subscriptions()).body.items.find((item) => item.slug === 'anthropic');
    expect(first?.ok).toBe(true);
    expect(first?.windows_sampled_at).not.toBeNull();
    const byLabel = Object.fromEntries((first?.windows ?? []).map((window) => [window.window, window]));
    expect(byLabel['5h']?.utilization_pct).toBe(41);
    expect(byLabel['7d']?.utilization_pct).toBe(63);
    expect(Date.parse(byLabel['5h']?.resets_at as string) / 1000).toBeCloseTo(now + 3_600, -1);

    vendorUsage.set(token, { short: { utilization: 77, resetsAt: now + 1_800 }, long: { utilization: 88, resetsAt: now + 43_200 } });
    expect((await runnerHost.stepHost()).outcome).toBe('completed');
    const second = (await runnerHost.api.subscriptions()).body.items.find((item) => item.slug === 'anthropic');
    const next = Object.fromEntries((second?.windows ?? []).map((window) => [window.window, window]));
    expect(next['5h']?.utilization_pct).toBe(77);
    expect(next['7d']?.utilization_pct).toBe(88);
    expect(Date.parse(next['5h']?.resets_at as string) / 1000).toBeCloseTo(now + 1_800, -1);

    // The counterpart saw this credential's bearer alone, once per pass.
    expect(vendorUsage.requests.map((request) => [request.vendor, request.token, request.status])).toEqual([
      ['anthropic', token, 200],
      ['anthropic', token, 200],
    ]);
  });
});

test.describe('with a credential the counterpart never minted', () => {
  test('the counterpart answers 401, and the subscription reads it as a credential_lapsed miss', async ({ vendorUsage, runnerHosts }) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bzh-cred-'));
    try {
      // A credential file whose token the counterpart never minted.
      const stranger = path.join(dir, 'stranger.json');
      fs.writeFileSync(stranger, JSON.stringify({ claudeAiOauth: { accessToken: 'never-minted', expiresAt: Date.UTC(2099, 0, 1) } }));
      const host = await runnerHosts.create({
        runnerConfig: { subscription: [{ slug: 'anthropic', credentials_path: stranger, sample_interval_seconds: 0n }] },
      });
      expect((await host.stepHost()).outcome).toBe('completed');
      const item = (await host.api.subscriptions()).body.items.find((entry) => entry.slug === 'anthropic');
      expect(item?.ok).toBe(false);
      expect(item?.miss_reason).toBe('credential_lapsed');
      expect(vendorUsage.requests.map((request) => [request.token, request.status])).toEqual([['never-minted', 401]]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});

test('the OpenAI shape is served for an openai subscription a profile declares with ${usageBase}', async ({ vendorUsage, runnerHosts }) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bzh-cred-'));
  try {
    const now = Math.floor(Date.now() / 1000);
    const levels = { short: { utilization: 19, resetsAt: now + 5_400 }, long: { utilization: 52, resetsAt: now + 200_000 } };
    const credential = vendorUsage.mintOpenAI(dir, levels);
    const host = await runnerHosts.create({
      runnerConfig: {
        subscription: [{ slug: 'openai', name: 'OpenAI', provider: 'openai', credentials_path: credential.path, usage_api_base: '${usageBase}', sample_interval_seconds: 0n }],
      },
    });
    expect((await host.stepHost()).outcome).toBe('completed');
    const item = (await host.api.subscriptions()).body.items.find((entry) => entry.slug === 'openai');
    expect(item?.ok).toBe(true);
    expect(Object.fromEntries((item?.windows ?? []).map((window) => [window.window, window.utilization_pct]))).toEqual({ '5h': 19, '7d': 52 });
    expect(vendorUsage.requests.filter((request) => request.vendor === 'openai').every((request) => request.token === credential.token && request.status === 200)).toBe(true);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('the counterpart keys by bearer: an unknown token and a wrong route are refused', async ({ vendorUsage, worldContext }) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bzh-cred-'));
  const status = async (route: string, token: string): Promise<number> =>
    (await worldContext.get(`${vendorUsage.url}${route}`, { headers: { authorization: `Bearer ${token}` }, failOnStatusCode: false })).status();
  try {
    const credential = vendorUsage.mintAnthropic(dir, defaultLevels());
    expect(await status('/api/oauth/usage', 'nope')).toBe(401);
    expect(await status('/api/oauth/usage', credential.token)).toBe(200);
    expect(await status('/codex/usage', credential.token)).toBe(401);
    expect(await status('/elsewhere', credential.token)).toBe(404);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a profile that does not ask for sampling keeps the product’s sample interval', async ({ runnerHost }) => {
  const file = fs.readFileSync(path.join(runnerHost.dir, 'blizzard-host.toml'), 'utf8');
  const anthropic = /\[\[subscription\]\][\s\S]*?(?=\n\[\[|\n\[)/.exec(file)?.[0] ?? '';
  expect(anthropic).toContain('slug = "anthropic"');
  expect(anthropic).toMatch(/sample_interval_seconds = 300/);
});
