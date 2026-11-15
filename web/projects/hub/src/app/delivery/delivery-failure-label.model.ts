export type DeliveryFailureLabel = 'merge conflict' | 'existing check failures' | 'timed out' | 'unknown failure';

export function deliveryDiagnostic(account: string, cause: string): string {
  return account === cause ? account : `${cause}\n${account}`;
}

/** Human wording for a structured delivery failure outcome or bounce cause. */
export function deliveryFailureLabel(value: string | null): DeliveryFailureLabel {
  switch (value) {
    case 'conflict':
      return 'merge conflict';
    case 'inherited-failure':
      return 'existing check failures';
    case 'poll-timeout':
      return 'timed out';
    default:
      return 'unknown failure';
  }
}
