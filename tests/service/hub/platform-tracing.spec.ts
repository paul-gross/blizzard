import { expect, test as base, type OtlpSink, type ReceivedSpan } from '../../harness/fixtures';

// Tracing export is hub-wide, so each test owns a hub. The hub's endpoint names the sink, which a test option cannot,
// so the sink starts first and `hubEnv` is built from it.
const test = base.extend<{ sink: OtlpSink }>({
  sink: async ({ otlpSink }, use) => {
    await use(await otlpSink());
  },
  hubEnv: async ({ sink }, use) => {
    await use({ OTEL_EXPORTER_OTLP_ENDPOINT: sink.endpoint });
  },
});

// The step-trace scopes — every other scope on the wire is a platform span's, whichever library opened it.
const FLEET_SCOPES = ['blizzard.hub.fleet_spans', 'blizzard.runner.runner_spans'];

function platformSpans(spans: ReceivedSpan[]): ReceivedSpan[] {
  return spans.filter((span) => !FLEET_SCOPES.includes(span.scope.name));
}

// The sink's record once the hub has stopped and flushed; a secret planted in the query string must be nowhere in it.
function platformSpansAfterStop(sink: OtlpSink): ReceivedSpan[] {
  const spans = sink.spans();
  expect(JSON.stringify(spans)).not.toContain('planted-secret');
  return platformSpans(spans);
}

test.describe('a hub with platform tracing on', () => {
  test.use({
    isolation: 'fresh-hub',
    hubConfig: { tracing: { platform: true, platform_sample_ratio: 1.0 } },
  });

  test('test_a_hub_with_platform_on_delivers_request_and_query_spans', async ({ sink, freshHub, api }) => {
    expect((await api.get('/api/chunks/ch_service', { params: { token: 'planted-secret' } })).status).toBe(404);
    await freshHub.stop();
    const spans = platformSpansAfterStop(sink);
    expect(spans.length, 'no platform span reached the sink').toBeGreaterThan(0);
    expect(new Set(spans.map((span) => span.resource['service.name']))).toEqual(new Set(['blizzard-hub']));
    const names = spans.map((span) => span.name);
    expect(names).toContain('GET /api/chunks/{chunk_id}');
    expect(
      names.some((name) => !['GET ', 'POST ', 'sweep '].some((prefix) => name.startsWith(prefix))),
      'no query span',
    ).toBe(true);
  });
});

test.describe('a hub with platform tracing off', () => {
  test.use({
    isolation: 'fresh-hub',
    hubConfig: { tracing: { platform: false, platform_sample_ratio: 1.0 } },
  });

  test('test_a_hub_with_platform_unset_delivers_no_platform_span', async ({ sink, freshHub, api }) => {
    expect((await api.get('/api/chunks/ch_service', { params: { token: 'planted-secret' } })).status).toBe(404);
    await freshHub.stop();
    expect(platformSpansAfterStop(sink)).toEqual([]);
  });
});
