import { type BeaconTone, compactRef } from 'fleet';

import { type RunnerProjects, servesProject } from '../core/panel-reads.model';
import type { HostEnvGroup } from './host-environments.model';
import type { HostRegistrationSummary } from './host-registration.model';
import type { HostRunnerRow } from './host-runner-row.model';

/** The host's compact ref beside its name, `null` before it registers under an id. */
export function hostRefLabel(hostId: string | null): string | null {
  return hostId ? compactRef(hostId) : null;
}

/** The host's light: green once registered, red when refused or cut off, amber on the way there. */
export function hostTone(registration: HostRegistrationSummary | null): BeaconTone {
  if (registration?.state === 'registered') return 'green';
  return registration?.state === 'refused' || registration?.state === 'unreachable' ? 'red' : 'amber';
}

/**
 * The runner rows the filters leave: the Runner filter, the runner it names; the Project filter,
 * the runners serving that project — a runner with no id, serving none, only while it is All.
 */
export function hostRowsInFilter(
  rows: readonly HostRunnerRow[],
  runner: string | null,
  project: string | null,
  byRunner: RunnerProjects,
): readonly HostRunnerRow[] {
  return rows.filter(
    (row) =>
      (runner === null || row.name === runner) &&
      (row.runnerId === null ? project === null : servesProject(byRunner.get(row.runnerId) ?? [], project)),
  );
}

/** "9 / 18 in use" across every workspace — a workspace the host does not declare counts its environments —
 * `null` while none is reported. */
export function envsInUseLabel(groups: readonly HostEnvGroup[]): string | null {
  if (groups.length === 0) return null;
  const inUse = groups.reduce((sum, group) => sum + group.inUse, 0);
  const total = groups.reduce((sum, group) => sum + (group.capacity ?? group.envs.length), 0);
  return `${inUse} / ${total} in use`;
}
