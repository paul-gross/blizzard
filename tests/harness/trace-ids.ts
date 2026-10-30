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
  return derive(traceIdRule(), chunkId);
}

function derive(rule: TraceIdRule, text: string): string {
  const digest = crypto.createHash('sha256').update(`${rule.prefix}${text}`).digest().subarray(0, rule.bytes);
  if (digest.every((byte) => byte === 0)) digest[digest.length - 1] = 1;
  return digest.toString('hex');
}

function rule(name: 'span' | 'chunk_span' | 'lifetime_span' | 'lifetime_trace'): TraceIdRule {
  const dictionary = JSON.parse(fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts', 'traces', 'dictionary.json'), 'utf8')) as { ids: Record<string, TraceIdRule> };
  return dictionary.ids[name]!;
}

export interface StepKey { chunkId: string; epoch: number; decisionId?: string }

export function stepKeyText(key: StepKey): string {
  return `${key.chunkId}/${key.epoch}${key.decisionId === undefined ? '' : `/gate/${key.decisionId}`}`;
}

export function spanId(key: StepKey, role: string, discriminator = ''): string {
  return derive(rule('span'), `${stepKeyText(key)}/${role}/${discriminator}`);
}

export function chunkSpanId(chunkId: string): string {
  return derive(rule('chunk_span'), `${chunkId}/chunk/`);
}

export function lifetimeTraceId(chunkId: string): string {
  return derive(rule('lifetime_trace'), chunkId);
}

export function lifetimeSpanId(chunkId: string, role = 'chunk/lifetime', discriminator = ''): string {
  return derive(rule('lifetime_span'), `${chunkId}/${role}/${discriminator}`);
}
