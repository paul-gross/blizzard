import * as http from 'node:http';
import { expect, test as base } from '../fixtures';
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

function post(url: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const request = http.request(url, { method: 'POST' }, (response) => {
      response.resume();
      resolve(response.statusCode as number);
    });
    request.once('error', reject);
    request.end();
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
