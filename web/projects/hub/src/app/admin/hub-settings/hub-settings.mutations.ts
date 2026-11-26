import { inject } from '@angular/core';
import { injectMutation, QueryClient } from '@tanstack/angular-query-experimental';
import { patchSettingsApiAdminSettingsPatch } from 'fleet/hub-api';
import { hubSettingsHistoryKey, hubSettingsKey } from '../../core/query-keys';

export function injectHubSettingsMutation() {
  const client = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (v: { key: string; revision: number; value: unknown; unset: boolean }) => {
      const { error } = await patchSettingsApiAdminSettingsPatch({ headers: { 'if-match': v.revision }, body: v.unset ? { unset: [v.key] } : { set: { [v.key]: v.value } } });
      if (error) throw error;
    },
    onSettled: () => Promise.all([client.invalidateQueries({ queryKey: hubSettingsKey }), client.invalidateQueries({ queryKey: hubSettingsHistoryKey })]).then(() => undefined),
  }));
}
