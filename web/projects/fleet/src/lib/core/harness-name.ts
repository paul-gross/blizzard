/** Operator-facing name for a harness identity, independent of its build version: the id with its underscores read as spaces. */
export function harnessName(id: string): string {
  return id.replaceAll('_', ' ');
}
