import * as fs from 'node:fs';
import * as path from 'node:path';
import { chunkTraceId, claudeCodeExports, expect, test } from '../fixtures';

test.use({ isolation: 'common' });

test.describe('the Claude Code export bodies', () => {
  test('the JSON form carries the recorded export with hex ids', () => {
    const [protobuf, json] = claudeCodeExports('logs');
    expect(protobuf!.contentType).toBe('application/x-protobuf');
    expect(json!.contentType).toBe('application/json');
    const record = JSON.parse(json!.body.toString('utf8')).resourceLogs[0].scopeLogs[0].logRecords[0];
    expect(record.traceId).toBe('0af7651916cd43dd8448eb211c80319c');
    expect(record.spanId).toBe('b7ad6b7169203331');
    expect(record.body).toEqual({ stringValue: 'claude_code.managed_settings_resolved' });
  });
});

test.describe('a chunk’s trace id', () => {
  test('matches every vector the trace dictionary publishes', () => {
    const dictionary = JSON.parse(fs.readFileSync(path.join(__dirname, '../../../contracts/traces/dictionary.json'), 'utf8')) as {
      ids: { vectors: { chunk_id: string; trace_id: string }[] };
    };
    expect(dictionary.ids.vectors.length).toBeGreaterThan(0);
    for (const vector of dictionary.ids.vectors) expect(chunkTraceId(vector.chunk_id), vector.chunk_id).toBe(vector.trace_id);
  });
});
