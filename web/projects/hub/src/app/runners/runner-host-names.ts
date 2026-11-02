import { type Provider, computed } from '@angular/core';

import { RUNNER_HOST_NAMES, RUNNER_NAMES } from 'fleet/shell';
import { injectHubRunnersQuery } from '../core/runners.query';
import { injectHubHostsQuery } from './hosts.query';

/** Each runner's host name by runner id, from the host registry's `runner_ids`. */
export function hostNamesByRunner(
  hosts: readonly { readonly host_name: string; readonly runner_ids?: readonly string[] }[],
): ReadonlyMap<string, string> {
  return new Map(hosts.flatMap((host) => (host.runner_ids ?? []).map((runnerId) => [runnerId, host.host_name] as const)));
}

/** The hub app's runner-to-host map, read off the host registry — every runner reference names its host from it —
 * and its runner-to-name map, read off the runner registry, for a view that holds only a runner id. */
export function provideRunnerHostNames(): Provider[] {
  return [
    {
      provide: RUNNER_HOST_NAMES,
      useFactory: () => {
        const hosts = injectHubHostsQuery();
        return computed(() => hostNamesByRunner(hosts.data() ?? []));
      },
    },
    {
      provide: RUNNER_NAMES,
      useFactory: () => {
        const runners = injectHubRunnersQuery();
        return computed(() => new Map((runners.data() ?? []).map((runner) => [runner.runner_id, runner.runner_name] as const)));
      },
    },
  ];
}
