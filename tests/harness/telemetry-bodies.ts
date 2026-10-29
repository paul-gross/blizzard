import * as fs from 'node:fs';
import * as path from 'node:path';
import { otlpJson } from './otlp-sink';

const RECORDED_ROOT = path.join(__dirname, '../fixtures/claude_code_telemetry');

/** One Claude Code export a worker's exporter posts: the same recorded body in each content type the receivers take. */
export interface ClaudeCodeExport {
  contentType: 'application/x-protobuf' | 'application/json';
  body: Buffer;
}

/**
 * Claude Code's recorded metric or log export, as protobuf and as OTLP/JSON. The `.pb` recording is the only source: the
 * JSON form is derived from it, with hex ids where the protobuf JSON mapping's are base64.
 */
export function claudeCodeExports(signal: 'metrics' | 'logs'): ClaudeCodeExport[] {
  const recorded = fs.readFileSync(path.join(RECORDED_ROOT, `${signal}.pb`));
  return [
    { contentType: 'application/x-protobuf', body: recorded },
    { contentType: 'application/json', body: Buffer.from(otlpJson(signal, recorded)) },
  ];
}
