import { expect, type RunnerHost, test } from '../../harness/fixtures';

// A guest at an adopted host's Settings tab, signed in through a real hub and the stub IdP. The host's file `[auth]`
// floors every hub identity to `guest` through `hub_role_default`, naming no `superuser`, so the hub's own superuser
// signs in to the panel as a guest. The guest reads every panel — the host's and the runner's settings, Workspaces and
// Subscriptions with an active and a retired record each, and History — and is offered no edit, create, retire, or
// enable control anywhere, a record's opened detail included.
test.use({ isolation: 'fresh-hub', runnerStarted: false });

const SUPERUSER = 'superuser@blizzard.test';
const NAME = 'runner-guest';
const TITLE = /blizzard runner/;

/** A runner the operator minted at the hub, adopted by a host that is booted under a guest floor and registered. */
async function adopt(host: RunnerHost): Promise<void> {
  // The first boot publishes the host's URL; the restart pinned to it declares that URL as its public origin.
  await host.restart();
  await host.stop();
  await host.reconfigure({ public_url: host.url, auth: { hub_role_default: 'guest' } });
  await host.restart();
  await host.step();
}

test('test_a_guest_sees_every_settings_panel_and_no_edit_or_verb_control', async ({ page, fleet, idp, runnerHosts }, testInfo) => {
  test.setTimeout(120_000);
  const minted = await fleet(NAME);
  const host = await runnerHosts.create({ runnerAdopted: { name: NAME, runnerId: minted.runnerId, token: minted.token } });
  await adopt(host);

  /** Runs the host's CLI over its socket, whose peer is the implicit superuser. */
  const cli = async (args: string[]): Promise<void> => {
    const result = await host.cli(args);
    expect(result.code, result.stderr).toBe(0);
  };
  await cli(['workspace', 'create', 'ws-active', `root="${testInfo.outputPath('ws-active')}"`]);
  await cli(['workspace', 'create', 'ws-retired', `root="${testInfo.outputPath('ws-retired')}"`]);
  await cli(['workspace', 'retire', 'ws-retired']);
  await cli(['subscription', 'create', 'sub-active', 'name="Active"', 'provider="anthropic"']);
  await cli(['subscription', 'create', 'sub-retired', 'name="Retired"', 'provider="anthropic"']);
  await cli(['subscription', 'retire', 'sub-retired']);

  await idp.profile(SUPERUSER);
  await page.goto(`${host.url}/settings`);
  await expect(page).toHaveTitle(TITLE);
  await expect(page.getByTestId('settings-page')).toBeVisible();

  // Every settings panel renders its keys and its in_force line, with no Edit on any key.
  for (const record of ['host', minted.runnerId]) {
    const panel = page.getByTestId(`settings-${record}`);
    await expect(page.getByTestId(`settings-${record}-in-force`)).toBeVisible();
    await expect(panel.locator(`[data-testid^="settings-${record}-value-"]`).first()).toBeVisible();
    await expect(panel.locator(`[data-testid^="settings-${record}-edit-"]`)).toHaveCount(0);
    await expect(page.getByTestId(`settings-history-${record}`)).toBeVisible();
  }

  const records = { workspace: ['ws-active', 'ws-retired'], subscription: ['sub-active', 'sub-retired'] } as const;
  for (const [kind, keys] of Object.entries(records)) {
    await expect(page.getByTestId(`records-${kind}`)).toBeVisible();
    await expect(page.getByTestId(`records-${kind}-create`)).toHaveCount(0);
    for (const key of keys) {
      const p = `records-${kind}-${key}`;
      await expect(page.getByTestId(`${p}-state`)).toHaveText(key.endsWith('retired') ? /retired/i : /active/i);
      await page.getByTestId(`${p}-open`).click();
      await expect(page.getByTestId(`${p}-detail`)).toBeVisible();
      await expect(page.getByTestId(`settings-history-${kind}-${key}`)).toBeVisible();
      for (const verb of ['edit', 'retire', 'enable']) await expect(page.getByTestId(`${p}-${verb}`)).toHaveCount(0);
    }
  }
});
