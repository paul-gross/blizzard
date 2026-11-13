import { computed, type Signal } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';

import type { runnerApi } from 'fleet';
import { type HostOrigin, type OriginProbe, type ProbeTarget, plainOrigin, selectOrigin } from './host-origin.model';

/** The longest one origin may take to answer before it counts as unreachable. */
export const ORIGIN_PROBE_TIMEOUT_MS = 4000;

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

/**
 * Probe one origin's runner `GET /api/health` from the browser. A rejection (network, CORS, mixed
 * content, Local Network Access), a timeout, a non-OK status or an unreadable body all settle as
 * `unreachable`; the probe never throws and never outlives `timeoutMs`.
 */
export async function probeOrigin(origin: string, fetchFn: Fetch, timeoutMs: number): Promise<OriginProbe> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<OriginProbe>((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve({ origin, kind: 'unreachable' });
    }, timeoutMs);
  });
  const answer = (async (): Promise<OriginProbe> => {
    try {
      const response = await fetchFn(`${origin}/api/health`, { mode: 'cors', credentials: 'omit', signal: controller.signal });
      if (!response.ok) return { origin, kind: 'unreachable' };
      const body = (await response.json()) as Partial<runnerApi.HealthResponse> | null;
      if (body === null || typeof body !== 'object') return { origin, kind: 'unreachable' };
      // The one place a pre-change shape is read: a runner older than `host_id` omits the key.
      if (!('host_id' in body)) return { origin, kind: 'legacy' };
      return { origin, kind: 'host', hostId: body.host_id ?? null };
    } catch {
      return { origin, kind: 'unreachable' };
    }
  })();
  try {
    return await Promise.race([answer, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

/** Probe every origin in parallel; the result keeps `urls` order. */
export function probeOrigins(
  urls: readonly string[],
  fetchFn: Fetch = (input, init) => fetch(input, init),
  timeoutMs: number = ORIGIN_PROBE_TIMEOUT_MS,
): Promise<OriginProbe[]> {
  return Promise.all(urls.map((origin) => probeOrigin(origin, fetchFn, timeoutMs)));
}

/**
 * The selected host's URL fact: probes the host's declared origins from this browser and links the
 * earliest that answers as the host. `null` target (no host selected) has no fact. A host declaring
 * no origins skips the probe and keeps today's plain `url` link.
 */
export function injectHostOrigin(target: () => ProbeTarget | null): Signal<HostOrigin> {
  const query = injectQuery(() => {
    const host = target();
    return {
      queryKey: ['host-origin-probe', host?.hostId ?? null, host?.urls ?? []],
      queryFn: () => probeOrigins(host?.urls ?? []),
      enabled: host !== null && host.urls.length > 0,
      retry: false,
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    };
  });
  return computed<HostOrigin>(() => {
    const host = target();
    if (host === null) return { kind: 'none' };
    if (host.urls.length === 0) return plainOrigin(host.url);
    const probes = query.data();
    return probes === undefined ? { kind: 'pending' } : selectOrigin(probes, host.hostId, host.url);
  });
}
