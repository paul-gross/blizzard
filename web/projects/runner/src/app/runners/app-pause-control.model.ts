/** This runner's brake as a pending local-pause flip for it will leave it, or `null` while no flip
 * names this runner (`bzh:frontend-pending-override`). */
export function pendingLocalPause(
  pendingLocalPauses: readonly { readonly runnerId: string; readonly paused: boolean }[],
  runnerId: string,
): boolean | null {
  return pendingLocalPauses.find((vars) => vars.runnerId === runnerId)?.paused ?? null;
}
