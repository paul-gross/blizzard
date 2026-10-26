import { expect, test } from '../../../harness/fixtures';

// The frames come from minting runners, a tenant-wide write, so the test owns its tenant.
test.use({ isolation: 'fresh-tenant' });

// A fraction of the stream's 15-second keepalive: an end only the keepalive would cause fails the test.
const ENDS_WITHIN_MS = 5_000;

test('test_revoking_a_members_membership_ends_their_open_stream_and_no_other', async ({ api, people, events, fleet }) => {
  const revoked = await people.create({ role: 'guest' });
  const kept = await people.create({ role: 'guest' });
  const revokedId = (await revoked.api.get('/api/me')).body.user_id as string;
  const revokedStream = await events.open({ as: revoked, reconnect: false });
  const keptStream = await events.open({ as: kept, reconnect: false });

  const before = await fleet('runner-before');
  const isRunner = (runnerId: string) => (frame: { event: string; data: { runner_id?: string } }) => frame.event === 'runner-changed' && frame.data.runner_id === runnerId;
  await revokedStream.waitFor(isRunner(before.runnerId));
  await keptStream.waitFor(isRunner(before.runnerId));

  const revoking = await api.delete(`/api/members/${revokedId}`);
  expect(revoking.status).toBe(204);
  await revokedStream.waitForEnd({ timeout: ENDS_WITHIN_MS });
  // Everything the revoked stream will ever hold: it ended, so nothing published later can reach it.
  const heldAtEnd = revokedStream.frames.map((frame) => frame.id);

  const after = await fleet('runner-after');
  // The kept stream's frame for the second runner is the barrier: the hub has published it by now, and the revoked stream
  // was removed from the broker before the 204.
  const barrier = await keptStream.waitFor(isRunner(after.runnerId));

  expect(keptStream.ended).toBe(false);
  expect(revokedStream.frames.filter((frame) => frame.event === 'runner-changed').map((frame) => frame.data.runner_id)).toEqual([before.runnerId]);
  // No frame reached the revoked stream after it ended, the barrier's own among them.
  expect(revokedStream.frames.map((frame) => frame.id)).toEqual(heldAtEnd);
  expect(heldAtEnd.every((id) => id < barrier.id)).toBe(true);
});
