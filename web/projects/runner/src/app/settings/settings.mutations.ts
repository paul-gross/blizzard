import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { hostSettingsWriteMutationKey, runnerSettingsWriteMutationKey } from '../core/mutation-keys';
import type { SettingsTarget, SettingWrite } from './settings.domain';
import { settingsKeyOf } from './settings.query';
import { toSettingsWrite } from './settings.translate';

/** One write to `target`'s settings record, sent against the revision the edit started from. */
export interface SettingsWriteVars {
  readonly target: SettingsTarget;
  readonly revision: number;
  readonly write: SettingWrite;
}

async function write(vars: SettingsWriteVars): Promise<void> {
  const headers = { 'if-match': String(vars.revision) };
  const body = toSettingsWrite(vars.write);
  const { error } =
    vars.target.kind === 'host'
      ? await runnerApi.patchHostSettingsApiHostSettingsPatch({ headers, body, throwOnError: false })
      : await runnerApi.patchSettingsRunnersRunnerIdApiSettingsPatch({
          path: { runner_id: vars.target.runnerId },
          headers,
          body,
          throwOnError: false,
        });
  if (error) throw error;
}

/**
 * `PATCH /api/host/settings` or `PATCH /runners/{runner_id}/api/settings` with `If-Match: <revision>`, so a write
 * against a stale read is refused with a 409 rather than overwriting a newer one. Settles once `target`'s record and
 * its change log have refetched; it writes nothing to the cache.
 */
export function injectSettingsWriteMutation(target: () => SettingsTarget) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => {
    const t = target();
    return {
      mutationKey: t.kind === 'host' ? hostSettingsWriteMutationKey : runnerSettingsWriteMutationKey(t.runnerId),
      mutationFn: write,
      onSettled: (_data: void | undefined, _error: unknown, vars: SettingsWriteVars) =>
        queryClient.invalidateQueries({ queryKey: settingsKeyOf(vars.target) }),
    };
  });
}
