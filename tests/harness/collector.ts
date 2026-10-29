import * as fs from 'node:fs';
import * as path from 'node:path';
import { freePort, startDaemon, stopDaemon } from './daemons/process';
import { startOtlpSink, type OtlpSink } from './otlp-sink';

export interface Collector {
  endpoint: string;
  received: OtlpSink;
  forwarded: OtlpSink;
  close(): Promise<void>;
}

export async function startCollector(dir: string, waitSeconds: number, binary = 'otelcol-contrib'): Promise<Collector> {
  fs.mkdirSync(dir, { recursive: true });
  const received = await startOtlpSink();
  const forwarded = await startOtlpSink();
  const port = await freePort();
  const overlay = path.join(dir, 'overlay.yaml');
  fs.writeFileSync(overlay, `receivers:\n  otlp:\n    protocols:\n      http:\n        endpoint: 127.0.0.1:${port}\nprocessors:\n  tail_sampling:\n    decision_wait: ${waitSeconds}s\n    policies:\n      - name: keep-step-roots\n        type: ottl_condition\n        ottl_condition:\n          error_mode: ignore\n          span:\n            - 'attributes["blizzard.step.outcome"] != nil'\nexporters:\n  otlphttp/received:\n    endpoint: ${received.endpoint}\n  otlphttp/forwarded:\n    endpoint: ${forwarded.endpoint}\nextensions:\n  health_check:\n    endpoint: 127.0.0.1:\${env:COLLECTOR_HEALTH_PORT}\nservice:\n  extensions: [health_check]\n  telemetry:\n    metrics:\n      level: none\n  pipelines:\n    traces:\n      processors: [tail_sampling, batch]\n      exporters: [otlphttp/forwarded]\n    traces/received:\n      receivers: [otlp]\n      processors: [batch]\n      exporters: [otlphttp/received]\n`);
  try {
    const daemon = await startDaemon({
      name: 'otelcol-contrib', cwd: dir, logPath: path.join(dir, 'collector.log'), healthPath: '/',
      launch: (healthPort) => ({ command: binary, args: [`--config=${path.resolve(__dirname, '../../packaging/otel-collector/collector.yaml')}`, `--config=${overlay}`], env: { ...process.env, COLLECTOR_HEALTH_PORT: String(healthPort), TRACE_STORE_ENDPOINT: 'localhost:4317', TRACE_STORE_INSECURE: 'true', HOSTED_TRACES_ENDPOINT: 'http://localhost:4318', HOSTED_TRACES_API_KEY: 'test' } }),
    });
    return { endpoint: `http://127.0.0.1:${port}`, received, forwarded, close: async () => { await stopDaemon(daemon); await Promise.all([received.close(), forwarded.close()]); } };
  } catch (error) {
    await Promise.all([received.close(), forwarded.close()]);
    throw error;
  }
}
