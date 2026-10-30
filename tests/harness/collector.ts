import * as fs from 'node:fs';
import * as path from 'node:path';
import * as crypto from 'node:crypto';
import { freePort, startDaemon, stopDaemon } from './daemons/process';
import { startOtlpSink, type OtlpSink } from './otlp-sink';

export interface Collector {
  endpoint: string;
  received: OtlpSink;
  forwarded: OtlpSink;
  /** Feed one unmarked span into the real collector, under a caller-owned trace id. */
  sendProbe(traceId: string): Promise<void>;
  /** A late probe of this trace reaches the collector's decision cache once sampling has decided it. */
  decision(traceId: string): Promise<'sampled' | 'dropped' | undefined>;
  close(): Promise<void>;
}

export async function startCollector(dir: string, waitSeconds: number, binary = 'otelcol-contrib'): Promise<Collector> {
  fs.mkdirSync(dir, { recursive: true });
  const received = await startOtlpSink();
  const forwarded = await startOtlpSink();
  const port = await freePort();
  const overlay = path.join(dir, 'overlay.yaml');
  fs.writeFileSync(overlay, `receivers:\n  otlp:\n    protocols:\n      http:\n        endpoint: 127.0.0.1:${port}\nprocessors:\n  tail_sampling:\n    decision_wait: ${waitSeconds}s\n    decision_cache:\n      sampled_cache_size: 10000\n      non_sampled_cache_size: 10000\n    policies:\n      - name: keep-step-roots\n        type: ottl_condition\n        ottl_condition:\n          error_mode: ignore\n          span:\n            - 'attributes["blizzard.step.outcome"] != nil'\nexporters:\n  otlphttp/received:\n    endpoint: ${received.endpoint}\n  otlphttp/forwarded:\n    endpoint: ${forwarded.endpoint}\nextensions:\n  health_check:\n    endpoint: 127.0.0.1:\${env:COLLECTOR_HEALTH_PORT}\nservice:\n  extensions: [health_check]\n  telemetry:\n    metrics:\n      level: none\n    logs:\n      level: debug\n      sampling:\n        enabled: false\n  pipelines:\n    traces:\n      processors: [tail_sampling, batch]\n      exporters: [otlphttp/forwarded]\n    traces/received:\n      receivers: [otlp]\n      processors: [batch]\n      exporters: [otlphttp/received]\n`);
  try {
    const daemon = await startDaemon({
      name: 'otelcol-contrib', cwd: dir, logPath: path.join(dir, 'collector.log'), healthPath: '/',
      launch: (healthPort) => ({ command: binary, args: [`--config=${path.resolve(__dirname, '../../packaging/otel-collector/collector.yaml')}`, `--config=${overlay}`], env: { ...process.env, COLLECTOR_HEALTH_PORT: String(healthPort), TRACE_STORE_ENDPOINT: 'localhost:4317', TRACE_STORE_INSECURE: 'true', HOSTED_TRACES_ENDPOINT: 'http://localhost:4318', HOSTED_TRACES_API_KEY: 'test' } }),
    });
    const endpoint = `http://127.0.0.1:${port}`;
    const logPath = path.join(dir, 'collector.log');
    const sendProbe = async (traceId: string): Promise<void> => {
      if (!/^[0-9a-f]{32}$/.test(traceId)) throw new Error('a collector probe needs a 32-digit hex trace id');
      const marker = crypto.randomBytes(8).toString('hex');
      const response = await fetch(`${endpoint}/v1/traces`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ resourceSpans: [{ scopeSpans: [{ scope: { name: 'collector.probe' }, spans: [{
          traceId, spanId: marker, name: 'sampling probe', startTimeUnixNano: '1', endTimeUnixNano: '2',
        }] }] }] }),
      });
      if (!response.ok) throw new Error(`collector probe answered ${response.status}: ${await response.text()}`);
    };
    return {
      endpoint, received, forwarded, sendProbe,
      async decision(traceId) {
        // An unmarked span cannot make the keep-step-roots policy sample a trace. The collector's own cache-hit log
        // names this trace only after its decision; a received span or an empty forward sink cannot provide that fact.
        await sendProbe(traceId);
        const log = fs.readFileSync(logPath, 'utf8');
        const lines = log.split('\n').filter((line) => line.includes(traceId) && line.includes('cache'));
        if (lines.some((line) => line.includes('non-sampled cache'))) return 'dropped';
        if (lines.some((line) => line.includes('sampled cache'))) return 'sampled';
        return undefined;
      },
      close: async () => { await stopDaemon(daemon); await Promise.all([received.close(), forwarded.close()]); },
    };
  } catch (error) {
    await Promise.all([received.close(), forwarded.close()]);
    throw error;
  }
}
