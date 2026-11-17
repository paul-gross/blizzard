import { bounceReason, parseBounceEnvelope } from './parse-bounce-envelope';

/** A missing account is distinct from an unknown diagnostic, which must survive verbatim. */
function bounceAccount(raw: string, envelope: ReturnType<typeof parseBounceEnvelope>): string | null {
  if (raw.trim() === '') return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
      const fields = value as Record<string, unknown>;
      if (['detail', 'cause'].some((key) => typeof fields[key] === 'string' && fields[key] !== '')) return bounceReason(raw);
      const keys = Object.keys(fields);
      const emptyLegacy = keys.every((key) => (key === 'detail' || key === 'cause') && fields[key] === '');
      const recognized = envelope !== null && keys.every((key) => Object.hasOwn(envelope, key));
      if (emptyLegacy || recognized) return null;
    }
  } catch {
    return raw;
  }
  return raw;
}

interface CompactBounce { identity: string | null; reason: string; full: string }

const capitalized = (name: string): string => `${name.charAt(0).toUpperCase()}${name.slice(1)}`;

function unlandedNames(envelope: NonNullable<ReturnType<typeof parseBounceEnvelope>>): string {
  return envelope.unlanded.map((repo) => (repo.pr ? `PR #${repo.pr.number} in ${repo.repo}` : repo.repo)).join(', ');
}

/** A record that carries `diagnostic` holds facts, and the sentence is derived here from them — nothing is matched
 * against the hub's old wrapper. The diagnostic, where there is one, leads; else the action's answer when it is the
 * cause; else the cause. */
function factBounce(envelope: NonNullable<ReturnType<typeof parseBounceEnvelope>>, recordedCause: string | null): CompactBounce {
  const identity = envelope.unlanded.map((repo) => `${repo.repo}${repo.pr ? ` · PR #${repo.pr.number}` : ''}`).join('; ') || null;
  const { action, cause, diagnostic } = envelope;
  const answer = action ? `: ${capitalized(action.name)} answered ${action.choice}` : '';
  const facts = `${unlandedNames(envelope) || 'delivery'} did not land${answer}`;
  const text = diagnostic ? diagnostic : null;
  const label = cause !== '' ? cause : recordedCause?.trim() ? recordedCause : 'bounced';
  const reason = text !== null ? text.replaceAll('`', '') : action && cause === action.choice ? `${capitalized(action.name)}: ${action.choice}` : label;
  return { identity, reason, full: text !== null ? `${facts}. ${text}` : facts };
}

/** Presentation only: never infer a cause from an action's generic failure choice. */
export function compactBounce(raw: string, recordedCause: string | null = null): CompactBounce {
  const envelope = parseBounceEnvelope(raw);
  if (envelope && envelope.diagnostic !== undefined) return factBounce(envelope, recordedCause);
  const account = bounceAccount(raw, envelope);
  const fallback = recordedCause?.trim() ? recordedCause : null;
  const full = account ?? fallback ?? 'bounced';
  const identity = envelope?.unlanded.map((repo) => `${repo.repo}${repo.pr ? ` · PR #${repo.pr.number}` : ''}`).join('; ') || null;
  const action = envelope?.action;
  if (envelope && action) {
    const names = envelope.unlanded.map((repo) => repo.pr ? `PR #${repo.pr.number} in ${repo.repo}` : repo.repo).join(', ');
    const generated = `${names} did not land: ${action.name} answered ${action.choice}`;
    // Only the exact producer-owned wrapper is redundant. Arbitrary diagnostics remain verbatim.
    const reason = full === generated || full === `${action.name} answered ${action.choice}` || (account === null && fallback === null)
      ? `${action.name.charAt(0).toUpperCase()}${action.name.slice(1)}: ${action.choice}`
      : full;
    return { identity, reason, full };
  }
  // Old records can carry only this known single-PR wrapper. Do not normalize other error prose.
  const legacy = /^(?:PR #(\d+) in ([\w.-]+\/[\w.-]+)) did not land: `?([\w-]+)`? answered ([\w-]+)$/.exec(full);
  if (!identity && legacy) {
    return { identity: `${legacy[2]} · PR #${legacy[1]}`, reason: `${legacy[3].charAt(0).toUpperCase()}${legacy[3].slice(1)}: ${legacy[4]}`, full };
  }
  return { identity, reason: full, full };
}
