import { type Provider, computed } from '@angular/core';

import { RUNNER_HOST_NAMES } from 'fleet/shell';
import { injectHubHostsQuery } from './hosts.query';

/** Each runner's host name by runner id, from the host registry's `runner_ids`. */
export function hostNamesByRunner(
  hosts: readonly { readonly host_name: string; readonly runner_ids?: readonly string[] }[],
): ReadonlyMap<string, string> {
  return new Map(hosts.flatMap((host) => (host.runner_ids ?? []).map((runnerId) => [runnerId, host.host_name] as const)));
}

/** The hub app's runner-to-host map, read off the host registry — every runner reference names its host from it. */
export function provideRunnerHostNames(): Provider[] {
  return [
    {
      provide: RUNNER_HOST_NAMES,
      useFactory: () => {
        const hosts = injectHubHostsQuery();
        return computed(() => hostNamesByRunner(hosts.data() ?? []));
      },
    },
  ];
}
