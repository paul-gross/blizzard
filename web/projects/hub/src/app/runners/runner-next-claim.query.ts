import { injectQuery } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';
import { effect, inject, signal } from '@angular/core';
import { TenantContext } from '../core/auth/tenant-context';
import { readUrgencyIcon } from '../core/urgency-icon.query';
import { hubUrgencyIconKey } from 'fleet';
import { prerequisiteStatuses } from './runner-next-claim.model';
import type { QueryObserverResult } from '@tanstack/angular-query-experimental';
import { hubApi, hubChunkKey, hubRunnerNextClaimKey, LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet';

/** The hub's projection is authoritative; the client does not repeat the claim walk. */
export function injectRunnerNextClaimQuery(runnerId: () => string) {
  return injectQuery(() => {
    const id = runnerId();
    return {
      queryKey: [...hubRunnerNextClaimKey, id],
      queryFn: async (): Promise<hubApi.NextClaimResponse> => {
        const { data, error } = await hubApi.getRunnerNextClaimApiRunnersRunnerIdNextClaimGet({
          path: { runner_id: id },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}

/** Read prerequisites individually from the tenant chunk endpoint, not the board window. */
export function injectPrerequisiteStatuses(ids: () => readonly string[]) {
  return injectQueries(() => ({
    queries: ids().map((id) => ({
      queryKey: hubChunkKey(id),
      queryFn: async () => {
        const { data, error } = await hubApi.getChunkApiChunksChunkIdGet({ path: { chunk_id: id }, throwOnError: false });
        if (error) throw error;
        return data!;
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    })),
    combine: (results) => prerequisiteStatuses(results as unknown as readonly QueryObserverResult<hubApi.ChunkDetail>[]),
  }));
}

/** Share icon blobs with other surfaces while owning the object URLs for this view. */
export function injectClaimUrgencyIconUrls(icons: () => readonly { key: string; digest: string }[]) {
  const tenant = inject(TenantContext);
  const queries = injectQueries(() => ({
    queries: icons().map((icon) => ({
      queryKey: hubUrgencyIconKey(tenant.current(), icon.key, icon.digest),
      enabled: tenant.current() !== null,
      queryFn: () => readUrgencyIcon(icon.key),
      staleTime: Infinity,
    })),
  }));
  const urls = signal<Readonly<Record<string, string>>>({});
  effect((onCleanup) => {
    const entries = icons().flatMap((icon, i) => {
      const blob = queries()[i]?.data();
      return blob ? [[icon.key, URL.createObjectURL(blob)] as const] : [];
    });
    urls.set(Object.fromEntries(entries));
    onCleanup(() => entries.forEach(([, url]) => URL.revokeObjectURL(url)));
  });
  return urls.asReadonly();
}
