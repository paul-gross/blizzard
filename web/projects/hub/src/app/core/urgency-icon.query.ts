import { effect, inject, signal, type Signal } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';
import { getUrgencyIconApiUrgenciesKeyIconGet, hubUrgencyIconKey } from 'fleet';
import { TenantContext } from './auth/tenant-context';

export async function readUrgencyIcon(key: string): Promise<Blob> {
  const { data, error } = await getUrgencyIconApiUrgenciesKeyIconGet({ path: { key }, parseAs: 'blob', throwOnError: false });
  if (error) throw error;
  if (!(data instanceof Blob) || data.size === 0 || !data.type.startsWith('image/')) throw new Error('Invalid urgency icon');
  return data;
}

/** Each observer owns its URL; the shared query owns only the immutable blob. */
export function injectUrgencyIconUrl(icon: () => { key: string; digest: string } | null): Signal<string | null> {
  const tenant = inject(TenantContext);
  const query = injectQuery(() => {
    const current = icon();
    const tenantId = tenant.current();
    return {
      queryKey: hubUrgencyIconKey(tenantId, current?.key ?? null, current?.digest ?? null),
      enabled: tenantId !== null && current !== null,
      queryFn: () => readUrgencyIcon(current!.key),
      staleTime: Infinity,
    };
  });
  const url = signal<string | null>(null);
  effect((onCleanup) => {
    const blob = query.data();
    if (!blob) { url.set(null); return; }
    const next = URL.createObjectURL(blob);
    url.set(next);
    onCleanup(() => URL.revokeObjectURL(next));
  });
  return url.asReadonly();
}
