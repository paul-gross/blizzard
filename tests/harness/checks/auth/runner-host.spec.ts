import * as fs from 'node:fs';
import * as path from 'node:path';
import { expect, test } from '../../fixtures';

test.describe('on the auth profile', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('init succeeds under the operator’s session, the host keeps none, and the socket steps while TCP is gated', async ({ runnerHost, api }) => {
    const runner = runnerHost.runners['runner-local'];
    const listed = await api.get('/api/runners');
    expect((listed.body.runners as { runner_id: string }[]).map((entry) => entry.runner_id)).toContain(runner?.id);

    // The host's own config home holds no session; the operator's was the config home init ran under.
    expect(fs.existsSync(path.join(runnerHost.home, '.config', 'blizzard', 'sessions.json'))).toBe(false);

    // Over the socket a pass completes; over TCP the step route is gated for want of a session.
    expect((await runnerHost.step()).outcome).toBe('completed');
    const gated = await runnerHost.api.raw.post(`/runners/${runner?.id}/api/ticks`);
    expect([401, 403]).toContain(gated.status);
  });
});
