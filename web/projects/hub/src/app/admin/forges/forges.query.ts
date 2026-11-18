import { injectQuery } from '@tanstack/angular-query-experimental';
import { getForgeApiForgesForgeGet, listForgesApiForgesGet, type ForgeDetail, type ForgeSummary } from 'fleet';
import { hubConfigListKey, hubConfigRecordKey } from '../../core/query-keys';
import type { Forge, ForgeRecord } from '../../forge/forge.domain';
import { fromForgeDetail, fromForgeSummary } from '../../forge/forge.translate';
import type { ForgeChoice, LandingPolicyHonour } from '../repositories/repository.domain';
import { fromForgeSummary as fromForgeChoice, fromPolicyHonour } from '../repositories/repository.translate';

const NOUN = 'forges';

function forgesOptions(retired: boolean) {
  return {
    queryKey: hubConfigListKey(NOUN, retired),
    queryFn: async (): Promise<ForgeSummary[]> => {
      const { data, error } = await listForgesApiForgesGet({ query: { include_retired: retired }, throwOnError: false });
      if (error) throw error;
      return data?.forges ?? [];
    },
  };
}

function forgeOptionsFor(forge: string | null) {
  return {
    queryKey: hubConfigRecordKey(NOUN, forge),
    enabled: forge !== null,
    queryFn: async (): Promise<ForgeDetail> => {
      const { data, error } = await getForgeApiForgesForgeGet({ path: { forge: forge! }, throwOnError: false });
      if (error) throw error;
      return data!;
    },
  };
}

/** The forges as the wire serves them — for the callers that still read the wire. */
export function injectForgesQuery(includeRetired: () => boolean) {
  return injectQuery(() => forgesOptions(includeRetired()));
}

/** The forges as the domain holds them, on the same cache entry as {@link injectForgesQuery}. */
export function injectForgeListQuery(includeRetired: () => boolean) {
  return injectQuery(() => ({
    ...forgesOptions(includeRetired()),
    select: (wire: ForgeSummary[]): Forge[] => wire.map(fromForgeSummary),
  }));
}

/** One forge as the wire serves it — for the callers that still read the wire. */
export function injectForgeQuery(key: () => string | null) {
  return injectQuery(() => forgeOptionsFor(key()));
}

/** One forge as the domain holds it, on the same cache entry as {@link injectForgeQuery}. */
export function injectForgeRecordQuery(key: () => string | null) {
  return injectQuery(() => ({
    ...forgeOptionsFor(key()),
    select: (wire: ForgeDetail): ForgeRecord => fromForgeDetail(wire),
  }));
}

/** {@link injectForgesQuery}'s read, on the same cache key, as the forges a repository form may offer. */
export function injectForgeChoicesQuery(includeRetired: () => boolean) {
  return injectQuery(() => ({
    ...forgesOptions(includeRetired()),
    select: (forges: ForgeSummary[]): readonly ForgeChoice[] => forges.map(fromForgeChoice),
  }));
}

/** {@link injectForgeQuery}'s read, on the same cache key, as which landing policies the forge can honour. */
export function injectForgeHonourQuery(key: () => string | null) {
  return injectQuery(() => ({
    ...forgeOptionsFor(key()),
    select: (forge: ForgeDetail): readonly LandingPolicyHonour[] => forge.landing_policies.map(fromPolicyHonour),
  }));
}
