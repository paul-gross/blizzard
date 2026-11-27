import { openLookup } from 'fleet/core';

export function deliveryDiagnostic(account: string, cause: string): string {
  return account === cause ? account : `${cause}\n${account}`;
}

const GENERIC = 'delivery failed';

/** The wording of each cause or outcome the hub names a reason for. The wire carries both as open
 * strings, so a value this table omits reads as itself rather than as a guess. */
const FAILURE_LABELS = openLookup<string, string>(
  {
    conflict: 'merge conflict',
    'inherited-failure': 'existing check failures',
    'poll-timeout': 'timed out',
    failure: GENERIC,
  },
  (raw) => raw,
);

/** Human wording for a structured delivery failure outcome or bounce cause; no reason reads as a generic failure. */
export function deliveryFailureLabel(value: string | null): string {
  return value === null ? GENERIC : FAILURE_LABELS(value);
}
