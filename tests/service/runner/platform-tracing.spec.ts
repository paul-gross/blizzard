import {
  chunkTraceId,
  claudeCodeExports,
  expect,
  HOLD_OPEN,
  type HostRoutes,
  type MockChunkSpec,
  type MockHub,
  type ReceivedSpan,
  type RunnerHost,
  runnerTest as test,
  runningLeaseWithWorker,
} from '../../harness/fixtures';

// A real stepped runner host exporting to the host-owned OTLP sink: the platform spans it opens, and the worker telemetry
// it forwards. The sink is read once the host has stopped and flushed. Each case posts to the runner over TCP and over
// the host's Unix socket.

const RUNNER = 'runner-local';
// The step-trace scopes — every other scope on the wire is a platform span's, whichever library opened it.
const FLEET_SCOPES = ['blizzard.hub.fleet_spans', 'blizzard.runner.runner_spans'];

function platformSpans(spans: ReceivedSpan[]): ReceivedSpan[] {
  return spans.filter((span) => !FLEET_SCOPES.includes(span.scope.name));
}

function transports(host: RunnerHost): [string, HostRoutes][] {
  return [
    ['tcp', host.api],
    ['socket', host.socketApi],
  ];
}

function holdingChunk(): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt: 'pass\n',
        judgement_prompt: HOLD_OPEN,
        choices: [{ name: 'pass', description: 'committed and green', to: 'done' }],
        retries_max: 1,
      },
    },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
  };
}

/** A running lease for a seeded chunk, and the worker headers that carry its recorded token as the worker's exporter sends it. */
async function leaseOfWorker(mockHub: MockHub, host: RunnerHost) {
  const seeded = await mockHub.seed.chunk(holdingChunk());
  expect(seeded.status).toBe(201);
  const chunkId = seeded.body.chunk_id;
  const { lease } = await runningLeaseWithWorker(host, chunkId);
  return { chunkId, leaseId: lease.lease_id, headers: host.workerHeaders(lease.lease_id, 'lease-token') };
}

test.describe('a runner host with platform tracing on', () => {
  test.use({ runnerOtlp: true, runnerConfig: { tracing: { platform: true, platform_sample_ratio: 1 } } });

  test('test_a_runner_host_delivers_server_spans_over_tcp_and_the_socket_and_tick_spans', async ({ runnerHost }) => {
    // No platform span leaves before the runner's first registration, which its first step makes.
    expect((await runnerHost.step()).outcome).toBe('completed');
    expect((await runnerHost.runners[RUNNER]!.status()).body.runner_id).toBeTruthy();
    expect((await runnerHost.api.health({ token: 'planted-secret' })).status).toBe(200);
    expect((await runnerHost.socketApi.health()).status).toBe(200);
    expect((await runnerHost.step()).outcome).toBe('completed');
    await runnerHost.stop();
    const sink = runnerHost.otlp!;
    const spans = platformSpans(sink.spans());
    expect(spans.length, 'no platform span reached the sink').toBeGreaterThan(0);
    expect(new Set(spans.map((span) => span.resource['service.name']))).toEqual(new Set(['blizzard-runner']));
    const names = spans.map((span) => span.name);
    expect(names.filter((name) => name === 'GET /api/health').length).toBeGreaterThanOrEqual(2);
    expect(names).toContain('tick');
    expect(names).toContain('Reap');
    expect(JSON.stringify([sink.spans(), sink.sumPoints(), sink.logRecords()])).not.toContain('planted-secret');
  });

  test('test_a_worker_span_posted_over_tcp_and_the_socket_reaches_the_real_exporter', async ({ runnerHost, mockHub }) => {
    const { chunkId, headers } = await leaseOfWorker(mockHub, runnerHost);
    const trace = chunkTraceId(chunkId);
    const body = JSON.stringify({
      resourceSpans: [
        {
          scopeSpans: [
            {
              scope: { name: 'blizzard.cli' },
              spans: [
                {
                  traceId: trace,
                  spanId: '00f067aa0ba902b7',
                  name: 'blizzard artifact create',
                  kind: 3,
                  startTimeUnixNano: '1000',
                  endTimeUnixNano: '2000',
                  attributes: [{ key: 'blizzard.cli.command', value: { stringValue: 'artifact create' } }],
                },
              ],
            },
          ],
        },
      ],
    });
    for (const [transport, routes] of transports(runnerHost)) {
      const posted = await routes.exportTelemetry(runnerHost.runners[RUNNER]!.id, 'traces', { body, contentType: 'application/json', headers });
      expect(posted.status, transport).toBe(200);
    }
    await runnerHost.stop();
    const cli = platformSpans(runnerHost.otlp!.spans()).filter((span) => span.resource['service.name'] === 'blizzard-cli' && span.name === 'blizzard artifact create');
    expect(cli, runnerHost.logTail()).toHaveLength(2);
    // The receiver drops a span outside the chunk's trace, so both arriving is the trace-parent proof.
    expect(cli.map((span) => span.traceId)).toEqual([trace, trace]);
  });
});

test.describe('a runner host forwarding Claude Code telemetry', () => {
  test.use({ runnerOtlp: true, runnerConfig: { tracing: { platform: true, platform_sample_ratio: 1, harness_telemetry: true } } });

  test('test_claude_code_metrics_and_logs_posted_over_tcp_and_the_socket_reach_the_real_exporters', async ({ runnerHost, mockHub }) => {
    const runner = runnerHost.runners[RUNNER]!;
    const { leaseId, headers } = await leaseOfWorker(mockHub, runnerHost);
    // The first step registered the runner: the identity every forwarded item is stamped with.
    const identity = (await runner.status()).body;
    for (const [transport, routes] of transports(runnerHost)) {
      for (const signal of ['metrics', 'logs'] as const) {
        for (const telemetry of claudeCodeExports(signal)) {
          const posted = await routes.exportTelemetry(runner.id, signal, { body: telemetry.body, contentType: telemetry.contentType, headers });
          expect(posted.status, `${transport} ${signal} ${telemetry.contentType}`).toBe(200);
        }
      }
    }
    await runnerHost.stop();
    const sink = runnerHost.otlp!;
    const points = sink.sumPoints();
    const records = sink.logRecords();
    expect(points, runnerHost.logTail()).toHaveLength(8);
    expect(records, runnerHost.logTail()).toHaveLength(24);
    for (const item of [...points, ...records]) {
      expect(item.resource['service.name']).toBe('blizzard-claude-code');
      expect(item.attributes['blizzard.lease.id']).toBe(leaseId);
      expect(item.attributes['blizzard.runner.id']).toBe(identity.runner_id);
      expect(item.attributes['blizzard.runner.name']).toBe(identity.runner_name);
      expect(identity.runner_name).toBe(runner.name);
    }
  });
});
