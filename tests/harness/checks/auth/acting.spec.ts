import * as fs from 'node:fs';
import { expect, test } from '../../fixtures';

test.describe('cli as a person', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('exits 0 for an admin person and non-zero for a guest', async ({ cli, people }) => {
    const admin = await people.create({ role: 'admin' });
    const guest = await people.create({ role: 'guest' });
    const created = await cli(['hub', 'project', 'create', 'made-by-admin', '--json'], { as: admin });
    expect(created.code, created.stdout + created.stderr).toBe(0);
    expect(created.stdout).toContain('made-by-admin');
    const refused = await cli(['hub', 'project', 'create', 'made-by-guest', '--json'], { as: guest });
    expect(refused.code, refused.stdout + refused.stderr).not.toBe(0);
  });

  test('logs a person in once per test, and each person in a config home of their own', async ({ cli, people }, testInfo) => {
    const first = await people.create({ role: 'admin' });
    const second = await people.create({ role: 'admin' });
    await cli(['hub', 'project', 'list', '--json'], { as: first });
    await cli(['hub', 'project', 'list', '--json'], { as: first });
    await cli(['hub', 'project', 'list', '--json'], { as: second });
    for (const person of [first, second]) {
      const sessions = testInfo.outputPath(`cli-config-home-${person.username}`, 'blizzard', 'sessions.json');
      expect(fs.existsSync(sessions), sessions).toBe(true);
    }
  });
});

test.describe('events as a person', () => {
  test.use({ isolation: 'common' });

  test('a subscription opened as a person receives a frame for something that person made', async ({ events, people, forge, project }) => {
    const person = await people.create({ role: 'admin' });
    const subscription = await events.open({ as: person });
    const issue = await forge.createIssue('blizzard/common', { title: 'made by a person' });
    const ingested = await person.api.post('/api/chunks', { data: { tokens: [`common:${issue.number}`], project: project.slug } });
    expect(ingested.ok, JSON.stringify(ingested.body)).toBe(true);
    const frame = await subscription.waitFor((candidate) => candidate.data?.chunk_id === ingested.body.chunk_id);
    expect(frame.data.chunk_id).toBe(ingested.body.chunk_id);
  });
});
