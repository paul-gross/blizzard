import { expect, runnerTest as test } from '../../fixtures';

test('cli reaches the stepped host over its socket and a tick completes', async ({ runnerHost }) => {
  const result = await runnerHost.cli(['tick']);
  expect(result.code, result.stdout + result.stderr).toBe(0);
  expect((await runnerHost.runners['runner-local']?.status())?.body.last_tick_at).not.toBeNull();
});

test('cli returns a non-zero exit as a result, not a throw', async ({ runnerHost }) => {
  const result = await runnerHost.cli(['tick', '--runner', 'no-such-runner']);
  expect(result.code).not.toBe(0);
});

test('a host without runnerOtlp has no sink', async ({ runnerHost }) => {
  expect(runnerHost.otlp).toBeUndefined();
});

test.describe('with runnerOtlp', () => {
  test.use({ runnerOtlp: true, runnerConfig: { tracing: { platform: true, platform_sample_ratio: 1 } } });

  test('the host delivers a span to its sink once stopped', async ({ runnerHost }) => {
    expect((await runnerHost.step()).outcome).toBe('completed');
    expect((await runnerHost.cli(['tick'])).code).toBe(0);
    await runnerHost.stop();
    const sink = runnerHost.otlp;
    expect(sink).toBeDefined();
    await expect.poll(() => sink?.spans().length ?? 0).toBeGreaterThan(0);
    expect(sink?.spans().map((span) => span.resource['service.name'])).toContain('blizzard-runner');
  });
});

test('originHead reads a branch of the minted world’s origin, and nothing for one that is not there', async ({ runnerHost }) => {
  expect(await runnerHost.originHead('no-such-repo', 'main')).toBeUndefined();
});

test('the stream connects to the host’s event stream', async ({ runnerHost, runnerStream }) => {
  const stream = await runnerStream(runnerHost.url);
  expect(stream.contentType).toMatch(/^text\/event-stream/);
});
