import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { BLIZZARD_ROOT } from './env';

interface TraceIdRule {
  bytes: number;
  prefix: string;
}

/** `ids.trace` of the published `contracts/traces/dictionary.json`: how a chunk's trace id derives. */
export function traceIdRule(): TraceIdRule {
  const dictionary = JSON.parse(fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts', 'traces', 'dictionary.json'), 'utf8')) as { ids: { trace: TraceIdRule } };
  return dictionary.ids.trace;
}

/** The chunk's trace id, hex: the leading bytes of the SHA-256 of the contract's prefix and the chunk id. An all-zero digest ends in `01`. */
export function chunkTraceId(chunkId: string): string {
  const rule = traceIdRule();
  const digest = crypto.createHash('sha256').update(`${rule.prefix}${chunkId}`).digest().subarray(0, rule.bytes);
  if (digest.every((byte) => byte === 0)) digest[digest.length - 1] = 1;
  return digest.toString('hex');
}
