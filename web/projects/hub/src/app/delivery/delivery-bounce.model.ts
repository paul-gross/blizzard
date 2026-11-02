/** The parts of a bounce envelope the page reads. */
export interface BounceEnvelope {
  readonly detail: string | null;
  readonly step: { readonly name: string; readonly exitCode: number | null } | null;
}

/** A bounce's envelope, parsed; an envelope that is not the expected JSON reads as carrying nothing. */
export function parseBounceEnvelope(envelope: string): BounceEnvelope {
  let raw: unknown;
  try {
    raw = JSON.parse(envelope);
  } catch {
    return { detail: null, step: null };
  }
  const record = (raw ?? {}) as { detail?: unknown; step?: { name?: unknown; exit_code?: unknown } | null };
  const step = record.step && typeof record.step.name === 'string' ? { name: record.step.name, exitCode: typeof record.step.exit_code === 'number' ? record.step.exit_code : null } : null;
  return { detail: typeof record.detail === 'string' && record.detail ? record.detail : null, step };
}

/** A bounce's reason: the envelope's `detail`, else `<step> exited <code>`, else `null`. */
export function bounceReason(envelope: BounceEnvelope): string | null {
  if (envelope.detail) return envelope.detail;
  if (envelope.step) return envelope.step.exitCode === null ? `${envelope.step.name} failed` : `${envelope.step.name} exited ${envelope.step.exitCode}`;
  return null;
}
