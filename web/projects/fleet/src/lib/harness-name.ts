/** Operator-facing name for a harness identity, independent of its build version. */
export function harnessName(id: string): string {
  return id === 'claude_code' || id === 'claude' ? 'claude code' : id;
}
