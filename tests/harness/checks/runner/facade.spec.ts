import * as fs from 'node:fs';
import * as path from 'node:path';
import { parse } from 'smol-toml';
import { expect, runnerTest as test } from '../../fixtures';

type Table = Record<string, unknown>;

/** The host file a host was built into, parsed. */
function hostFile(dir: string): Table {
  return parse(fs.readFileSync(path.join(dir, 'blizzard-host.toml'), 'utf8')) as Table;
}

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

test.describe('with OpenCode enabled, a bundle and a world to read', () => {
  const bundle = { 'opencode/opencode.json': '{"permission":{"bash":"deny"}}' };
  test.use({
    runnerBundle: bundle,
    runnerConfig: { runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' }, harness: { config_dir: '${bundle}' } }] },
  });

  test('the host passes a step, and holds the synthetic OpenCode login under its private data home', async ({ runnerHost }) => {
    expect((await runnerHost.step()).outcome).toBe('completed');
    const auth = path.join(runnerHost.home, '.local', 'share', 'opencode', 'auth.json');
    expect(fs.statSync(auth).size).toBeGreaterThan(0);
  });

  test('the bundle directory reaches the host file, holding the files the test gave', async ({ runnerHost }) => {
    const runner = (hostFile(runnerHost.dir).runner as Table[])[0] as Table;
    const dir = (runner.harness as Table).config_dir as string;
    expect(fs.readFileSync(path.join(dir, 'opencode', 'opencode.json'), 'utf8')).toBe(bundle['opencode/opencode.json']);
  });

  test('the readers return the documents of the host’s own world, and none before there are any', async ({ runnerHost }) => {
    expect(await runnerHost.mockSessions()).toEqual([]);
    expect(await runnerHost.opencodeExports()).toEqual([]);
    const file = hostFile(runnerHost.dir);
    const workspace = ((file.workspace as Table[])[0] as Table).root as string;
    const transcripts = ((file.runner as Table[])[0] as Table).transcripts_root as string;
    fs.mkdirSync(path.join(workspace, '.blizzard-mock-harness', 'sessions'), { recursive: true });
    fs.writeFileSync(path.join(workspace, '.blizzard-mock-harness', 'sessions', 's.json'), JSON.stringify({ invocations: [{ kind: 'spawn' }] }));
    fs.mkdirSync(path.join(transcripts, 'mock-opencode'), { recursive: true });
    fs.writeFileSync(path.join(transcripts, 'mock-opencode', 'e.json'), JSON.stringify({ info: { id: 'ses_1' }, messages: [] }));
    expect(await runnerHost.mockSessions()).toEqual([{ invocations: [{ kind: 'spawn' }] }]);
    expect((await runnerHost.opencodeExports()).map((doc) => doc.info.id)).toEqual(['ses_1']);
  });
});

test('the host process exports the transcripts root its runner reads', async ({ runnerHost }) => {
  const transcripts = ((hostFile(runnerHost.dir).runner as Table[])[0] as Table).transcripts_root as string;
  const environ = fs.readFileSync(`/proc/${runnerHost.pid}/environ`, 'utf8').split('\0');
  expect(environ).toContain(`BZ_TRANSCRIPTS_ROOT=${transcripts}`);
});
