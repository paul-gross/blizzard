import { errorMessage } from 'fleet';

/** A role-assignment refusal's message through `errorMessage` (its 422 handling included), a generic failure line when it carries none, or `null` with no error. */
export function assignRoleErrorText(error: unknown): string | null {
  if (!error) return null;
  return errorMessage(error, 'Failed to change role.');
}

/** The user ids a role assignment is pending for, from the pending mutations' variables. */
export function pendingRoleUserIds(pending: readonly { readonly userId: string }[]): readonly string[] {
  return pending.map((vars) => vars.userId);
}
