import { injectQuery } from '@tanstack/angular-query-experimental';
import { getUrgencyApiUrgenciesKeyGet, hubConfigRecordKey, type UrgencyView } from 'fleet';

export function injectUrgencyQuery(key: () => string | null) {
  return injectQuery(() => {
    const value = key();
    return {
      queryKey: hubConfigRecordKey('urgencies', value), enabled: value !== null,
      queryFn: async (): Promise<UrgencyView> => {
        const { data, error } = await getUrgencyApiUrgenciesKeyGet({ path: { key: value! }, throwOnError: false });
        if (error) throw error;
        return data!;
      },
    };
  });
}

import { getSettingsApiSettingsGet, type SettingsView } from 'fleet';
import { readUrgencyIcon } from '../../core/urgency-icon.query';
import { inject } from '@angular/core';
import { TenantContext } from '../../core/auth/tenant-context';

export function injectSettingsQuery() {
  return injectQuery(() => ({
    queryKey: ['hub', 'config', 'settings'],
    queryFn: async (): Promise<SettingsView> => {
      const { data, error } = await getSettingsApiSettingsGet({ throwOnError: false });
      if (error) throw error;
      return data!;
    },
  }));
}

/** Authenticated image bytes for the form preview, never a raw API URL. */
export function injectUrgencyIconData(key: () => string | null, digest: () => string | null) {
  const tenant = inject(TenantContext);
  return injectQuery(() => {
    const id = key();
    const hash = digest();
    return {
      queryKey: ['hub', 'urgency-form-icon', tenant.current(), id, hash],
      enabled: tenant.current() !== null && id !== null && hash !== null,
      staleTime: Infinity,
      queryFn: async (): Promise<string> => {
        const blob = await readUrgencyIcon(id!);
        const bytes = new Uint8Array(await blob.arrayBuffer());
        let binary = '';
        for (const byte of bytes) binary += String.fromCharCode(byte);
        return `data:${blob.type};base64,${btoa(binary)}`;
      },
    };
  });
}
