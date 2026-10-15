// exemplar: stand-in for the permit keys production adds to `query-keys.ts`; the exemplar overlay appends these lines.

/** The permit listing — `GET /api/permits`. */
export const hubPermitsKey = ['hub', 'permits'] as const;
/** One permit — `GET /api/permits/{permit_id}`; `null` for the disabled-query rest state. */
export function hubPermitKey(permitId: string | null): readonly unknown[] {
  return ['hub', 'permit', permitId];
}
