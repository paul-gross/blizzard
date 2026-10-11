/** How many records an admin tab holds, `null` while its read has not answered (or is not the actor's to make). */
export function tabCount(records: readonly unknown[] | undefined, narrowedTo: ReadonlySet<string> | null = null): number | null {
  if (!Array.isArray(records)) return null;
  if (narrowedTo === null) return records.length;
  return records.filter((record) => narrowedTo.has((record as { readonly name: string }).name)).length;
}
