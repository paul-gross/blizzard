import { computed, effect, inject, signal, type Signal } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';
import { getUrgencyIconApiUrgenciesKeyIconGet } from 'fleet';
import { TenantContext } from './auth/tenant-context';
import { hubUrgencyIconKey } from './query-keys';

/** An urgency's image mark, addressed by its key and content digest. */
export interface UrgencyIconRef {
  readonly key: string;
  readonly digest: string;
}

export async function readUrgencyIcon(key: string): Promise<Blob> {
  const { data, error } = await getUrgencyIconApiUrgenciesKeyIconGet({ path: { key }, parseAs: 'blob', throwOnError: false });
  if (error) throw error;
  if (!(data instanceof Blob) || data.size === 0 || !data.type.startsWith('image/')) throw new Error('Invalid urgency icon');
  return data;
}

/** The one cache entry every icon observer shares: the immutable Blob of one tenant's icon at one digest. */
export function urgencyIconQueryOptions(tenantId: string | null, icon: UrgencyIconRef | null) {
  return {
    queryKey: hubUrgencyIconKey(tenantId, icon?.key ?? null, icon?.digest ?? null),
    enabled: tenantId !== null && icon !== null,
    queryFn: () => readUrgencyIcon(icon!.key),
    staleTime: Infinity,
  };
}

/** Publishes object URLs for the given Blobs, revoking each when its Blob changes or the owner is destroyed. */
function objectUrls(blobs: () => readonly (readonly [string, Blob | undefined])[]): Signal<Readonly<Record<string, string | undefined>>> {
  const urls = signal<Readonly<Record<string, string | undefined>>>({});
  effect((onCleanup) => {
    const entries = blobs().flatMap(([key, blob]) => (blob ? [[key, URL.createObjectURL(blob)] as const] : []));
    urls.set(Object.fromEntries(entries));
    onCleanup(() => entries.forEach(([, url]) => URL.revokeObjectURL(url)));
  });
  return urls.asReadonly();
}

/** One icon's object URL; this observer owns the URL, the shared query owns only the Blob. */
export function injectUrgencyIconUrl(icon: () => UrgencyIconRef | null): Signal<string | null> {
  const tenant = inject(TenantContext);
  const query = injectQuery(() => urgencyIconQueryOptions(tenant.current(), icon()));
  const urls = objectUrls(() => [['icon', query.data()] as const]);
  return computed(() => urls()['icon'] ?? null);
}

/** Many icons' object URLs, keyed by urgency key. */
export function injectUrgencyIconUrls(icons: () => readonly UrgencyIconRef[]): Signal<Readonly<Record<string, string | undefined>>> {
  const tenant = inject(TenantContext);
  const queries = injectQueries(() => ({
    queries: icons().map((icon) => urgencyIconQueryOptions(tenant.current(), icon)),
  }));
  return objectUrls(() => icons().map((icon, i) => [icon.key, queries()[i]?.data()] as const));
}

/** The editor's `data:<type>;base64,…` form value, derived from the shared Blob. */
export async function urgencyIconDataUrl(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `data:${blob.type};base64,${btoa(binary)}`;
}

/** Authenticated image bytes for the form preview as a data URL, never a raw API URL. */
export function injectUrgencyIconDataUrl(icon: () => UrgencyIconRef | null): Signal<string | null> {
  const tenant = inject(TenantContext);
  const query = injectQuery(() => urgencyIconQueryOptions(tenant.current(), icon()));
  const url = signal<string | null>(null);
  effect((onCleanup) => {
    const blob = query.data();
    let live = true;
    onCleanup(() => { live = false; });
    if (!blob) { url.set(null); return; }
    void urgencyIconDataUrl(blob).then((value) => { if (live) url.set(value); });
  });
  return url.asReadonly();
}
