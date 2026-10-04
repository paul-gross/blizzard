/** A role-assignment refusal's own `detail` message, a generic failure line when it carries none, or `null` with no error. */
export function assignRoleErrorText(error: unknown): string | null {
  if (!error) return null;
  const detail = (error as { detail?: unknown }).detail;
  return typeof detail === 'string' ? detail : 'Failed to change role.';
}
