import * as path from 'node:path';
import { type APIRequestContext, test as plain } from '@playwright/test';
import { uniqueName } from '../../base';
import { HubInternal } from '../../client/internal';
import { stopDaemon } from '../../daemons/process';
import { runDir } from '../../env';
import { expect, test } from '../../fixtures';
import { readProjectHubs, startStack } from '../../hubs';
import { discoverProfiles, profileEnv, readProfile } from '../../profiles';

/** What a hub answers to a graph naming `test.answer`, a test action that joins the registry only behind the fence. */
async function mintTestAnswer(request: APIRequestContext, url: string, tenantId: string, name: string): Promise<{ status: number; text: string }> {
  const definition = {
    name,
    entry: 'pick',
    nodes: {
      pick: {
        executor: 'hub',
        action: 'test.answer',
        with: { choice: 'onward' },
        judgement: { choices: { onward: { description: 'On.', to: 'done' }, failure: { description: 'The pass failed.', to: 'pick' } } },
      },
    },
  };
  const response = await request.post(`${url}/api/graphs`, {
    headers: { 'X-Blizzard-Tenant': tenantId },
    data: { definition_yaml: JSON.stringify(definition) },
  });
  return { status: response.status(), text: await response.text() };
}

plain('only the actions profile resolves a fence environment, across every discovered profile', () => {
  const fenced = discoverProfiles().filter((folder) => Object.keys(profileEnv(folder.name)).length > 0);
  expect(fenced.map((folder) => folder.name)).toEqual(['actions']);
  expect(profileEnv('actions')).toEqual({ BLIZZARD_TEST_ACTIONS: '1', BLIZZARD_CRASH_FENCE: '1' });
});

plain('the actions profile is the default profile’s content', () => {
  const [base, actions] = [discoverProfiles().find((folder) => folder.name === ''), discoverProfiles().find((folder) => folder.name === 'actions')];
  expect(readProfile(actions?.file ?? '')).toEqual(readProfile(base?.file ?? ''));
});

plain('the default shared hub refuses the test actions as unknown', async ({ playwright }) => {
  const request = await playwright.request.newContext();
  const [hub] = readProjectHubs('service-hub').hubs;
  expect(hub?.commonTenantId).toBeDefined();
  const refused = await mintTestAnswer(request, hub!.url, hub!.commonTenantId!, `refused-${Date.now()}`);
  expect(refused.status).toBe(422);
  expect(refused.text).toContain('unknown action `test.answer`');
  await request.dispose();
});

plain('a default-profile fresh hub refuses the test actions as unknown', async ({ playwright }, testInfo) => {
  const request = await playwright.request.newContext();
  const stack = await startStack(path.join(runDir(), 'fresh', uniqueName(testInfo)), 0, {}, profileEnv(''));
  try {
    const [seeded] = (await new HubInternal(stack.coordinates.url).listTenants()).filter((candidate) => candidate.state === 'open');
    const refused = await mintTestAnswer(request, stack.coordinates.url, seeded!.tenant_id, uniqueName(testInfo));
    expect(refused.status).toBe(422);
    expect(refused.text).toContain('unknown action `test.answer`');
  } finally {
    await request.dispose();
    await Promise.all(stack.coordinates.daemons.map(stopDaemon));
  }
});

test.describe('the actions shared hub', () => {
  test.use({ isolation: 'common' });

  test('admits the test actions', async ({ testHub, tenant }, testInfo) => {
    const admitted = await mintTestAnswer(testHub.context, testHub.coordinates.url, tenant.id, uniqueName(testInfo));
    expect(admitted.status, admitted.text).toBeLessThan(300);
  });
});

test.describe('an actions fresh hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test('admits the test actions, and again after a reset', async ({ testHub, freshHub, tenant }, testInfo) => {
    testInfo.setTimeout(90_000);
    const admitted = await mintTestAnswer(testHub.context, testHub.coordinates.url, tenant.id, uniqueName(testInfo));
    expect(admitted.status, admitted.text).toBeLessThan(300);
    await freshHub.stop();
    await freshHub.reset();
    const [seeded] = (await new HubInternal(testHub.coordinates.url).listTenants()).filter((candidate) => candidate.state === 'open');
    const again = await mintTestAnswer(testHub.context, testHub.coordinates.url, seeded!.tenant_id, uniqueName(testInfo, 'again-'));
    expect(again.status, again.text).toBeLessThan(300);
  });
});
