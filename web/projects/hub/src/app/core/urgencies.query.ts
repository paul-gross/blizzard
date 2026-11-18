import { injectQuery } from '@tanstack/angular-query-experimental';
import { listUrgenciesApiUrgenciesGet, type UrgencyView } from 'fleet';
import { hubUrgenciesKey } from './query-keys';
import { fromUrgencyView } from './urgency.translate';

export const URGENCIES_POLL_MS = 15_000;
export async function readUrgencies(): Promise<readonly UrgencyView[]> {
  const { data, error } = await listUrgenciesApiUrgenciesGet({ query: { retired: true }, throwOnError: false });
  if (error) throw error;
  return data!.urgencies;
}
function urgenciesQueryOptions() {
  return { queryKey: hubUrgenciesKey, queryFn: readUrgencies, refetchInterval: URGENCIES_POLL_MS, refetchOnWindowFocus: true, refetchOnReconnect: true };
}
export function domainUrgenciesQueryOptions() {
  return { ...urgenciesQueryOptions(), select: (wire: readonly UrgencyView[]) => wire.map(fromUrgencyView) };
}

export function injectDomainUrgenciesQuery() {
  return injectQuery(() => domainUrgenciesQueryOptions());
}
