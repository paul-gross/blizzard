import * as fs from 'node:fs';
import * as http from 'node:http';
import * as path from 'node:path';
import { claudeCodeExports, expect, test as base } from '../fixtures';
import type { OtlpSink } from '../otlp-sink';

// A test option cannot name a value only a fixture knows, so the sink starts first and the hub's env is built from it.
const test = base.extend<{ sink: OtlpSink }>({
  sink: async ({ otlpSink }, use) => {
    await use(await otlpSink());
  },
  hubEnv: async ({ sink }, use) => {
    await use({ OTEL_EXPORTER_OTLP_ENDPOINT: sink.endpoint });
  },
});

function post(url: string, body?: Buffer): Promise<number> {
  return new Promise((resolve, reject) => {
    const request = http.request(url, { method: 'POST', headers: { 'content-type': 'application/x-protobuf' } }, (response) => {
      response.resume();
      resolve(response.statusCode as number);
    });
    request.once('error', reject);
    request.end(body);
  });
}

test.describe('the otlpSink fixture', () => {
  test.use({ isolation: 'fresh-hub' });

  test('answers 404 on a path that is not an export path, and 200 on the three that are', async ({ sink }) => {
    expect(await post(`${sink.endpoint}/v1/other`)).toBe(404);
    for (const signal of ['traces', 'metrics', 'logs']) {
      expect(await post(`${sink.endpoint}/v1/${signal}`), signal).toBe(200);
    }
  });
});

test.describe('the otlpSink fixture decoding what it receives', () => {
  test.use({ isolation: 'fresh-hub' });

  test('reads a metric export as its sum data points and a log export as its records, each with its resource', async ({ sink }) => {
    const [metrics] = claudeCodeExports('metrics');
    const [logs] = claudeCodeExports('logs');
    expect(await post(`${sink.endpoint}/v1/metrics`, metrics!.body)).toBe(200);
    expect(await post(`${sink.endpoint}/v1/logs`, logs!.body)).toBe(200);
    const points = sink.sumPoints();
    expect(points).toHaveLength(2);
    expect(points[0]?.resource['service.name']).toBe('claude-code');
    expect(points[0]?.attributes['start_type']).toBe('fresh');
    const records = sink.logRecords();
    expect(records).toHaveLength(6);
    expect(records[0]?.resource['service.name']).toBe('claude-code');
    expect(records[0]?.attributes['event.name']).toBe('managed_settings_resolved');
  });

  test('reads a span’s trace id as hex', async ({ sink }) => {
    expect(await post(`${sink.endpoint}/v1/traces`, fs.readFileSync(path.join(__dirname, '../../fixtures/claude_code_telemetry/traces.pb')))).toBe(200);
    const spans = sink.spans();
    expect(spans.length).toBeGreaterThan(0);
    for (const span of spans) expect(span.traceId).toMatch(/^[0-9a-f]{32}$/);
  });
});

test.describe('a fresh hub exporting platform spans', () => {
  test.use({ isolation: 'fresh-hub', hubConfig: { tracing: { platform: true, platform_sample_ratio: 1.0 } } });

  test('delivers a span for a request to the sink once stopped', async ({ sink, freshHub, issue, api }) => {
    const filed = await issue('traced');
    await api.get(`/api/chunks/${filed.chunk.chunk_id}`);
    await freshHub.stop();
    const span = sink.spans().find((candidate) => candidate.name === 'GET /api/chunks/{chunk_id}');
    expect(span, JSON.stringify(sink.spans().map((candidate) => candidate.name))).toBeDefined();
    expect(span?.resource['service.name']).toBe('blizzard-hub');
  });
});
