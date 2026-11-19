import { type RunnerEventType } from '../api/runner';
import { openLookup } from '../core/open-lookup';
import { type RunnerStreamEvent } from './runner-stream.domain';

type KnownKind = Exclude<RunnerStreamEvent['kind'], 'unknown'>;

const eventKind = openLookup<RunnerEventType, KnownKind | 'unknown'>(
  {
    'lease-changed': 'lease',
    'ask-changed': 'ask',
    'escalation-changed': 'escalation',
    'takeover-changed': 'takeover',
    'environment-changed': 'environment',
    'fact-changed': 'fact',
  },
  () => 'unknown',
);
/** Every known wire name is subscribed; the table is exhaustive over the generated boundary. */
export const RUNNER_EVENT_TYPES: readonly string[] = eventKind.keys;

/** Translate an untrusted frame; unrecognized or unusable frames stale nothing. */
export function translateRunnerStreamEvent(type: string, data: unknown): RunnerStreamEvent {
  const kind = eventKind(type);
  if (kind === 'unknown' || data === null || typeof data !== 'object' || Array.isArray(data)) return { kind: 'unknown', type, raw: data };
  if (kind === 'environment') return { kind };

  const payload = data as Record<string, unknown>;
  if (typeof payload['runner_id'] !== 'string' || !payload['runner_id']) return { kind: 'unknown', type, raw: data };
  const chunkId = payload['chunk_id'];
  if (chunkId != null && (typeof chunkId !== 'string' || !chunkId)) return { kind: 'unknown', type, raw: data };
  return { kind, runnerId: payload['runner_id'], chunkId: chunkId ?? null };
}
