import type { RoutineView } from 'fleet';

/** The display `name` of the routine whose `slug` is `slug`, read off the loaded routines — the slug itself while the
 * routine is not loaded, so a row naming a routine always reads as something. */
export function routineDisplayName(slug: string, routines: readonly RoutineView[] | undefined): string {
  return routines?.find((r) => r.slug === slug)?.name ?? slug;
}
