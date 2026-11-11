import { injectQuery } from '@tanstack/angular-query-experimental';
import { hubUrgenciesKey, listUrgenciesApiUrgenciesGet, type UrgencyView } from 'fleet';

export const URGENCIES_POLL_MS = 15_000;
export async function readUrgencies(): Promise<readonly UrgencyView[]> {
  const { data, error } = await listUrgenciesApiUrgenciesGet({ query: { retired: true }, throwOnError: false });
  if (error) throw error;
  return data!.urgencies;
}
export function urgenciesQueryOptions() {
  return { queryKey: hubUrgenciesKey, queryFn: readUrgencies, refetchInterval: URGENCIES_POLL_MS, refetchOnWindowFocus: true, refetchOnReconnect: true };
}
export function injectUrgenciesQuery() {
  return injectQuery(() => urgenciesQueryOptions());
}
