/** A bounce's envelope, read: `cause` and `detail` (each `null` when absent or not a
 * string) and every further key as a stringified `[key, value]` entry, in envelope order. */
export interface BounceEnvelope {
  readonly cause: string | null;
  readonly detail: string | null;
  readonly extras: readonly (readonly [string, string])[];
}

function stringify(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** Runtime read of the one-line JSON envelope the hub records on a refused worker
 * result. `null` when `raw` is not a JSON object — the caller falls back to the raw text. */
export function parseBounceEnvelope(raw: string): BounceEnvelope | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null;
  const record = parsed as Record<string, unknown>;
  const { cause, detail, ...rest } = record;
  return {
    cause: typeof cause === 'string' ? cause : null,
    detail: typeof detail === 'string' ? detail : null,
    extras: Object.entries(rest).map(([key, value]) => [key, stringify(value)] as const),
  };
}

/** The envelope as one readable line: its `detail` with markdown code ticks dropped, else
 * its `cause`, else — for an unparseable or field-less envelope — the raw text. */
export function bounceReason(raw: string): string {
  const envelope = parseBounceEnvelope(raw);
  const text = envelope?.detail ?? envelope?.cause;
  return text ? text.replaceAll('`', '') : raw;
}
