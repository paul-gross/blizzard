/**
 * Runner display names — how a runner is named outside the runners list. Where the runner's
 * host is known it is `host/name` (`atlas/r-claude`): a name is unique on its host, so the
 * host keeps two same-named runners apart. Where it is not, the compact id leads instead,
 * then the separator and the name (`rn_01KX…ABF3` named `r-claude` → `R-ABF3.r-claude`).
 * A view that carries no name renders the compact id in the name's place.
 *
 * This is the single owner of that rendering (`bzh:frontend-formatters`).
 */

import { InjectionToken, type Signal, inject, signal } from '@angular/core';

import { compactRef } from './compact-ref';

/** What joins a runner's compact id to its name in {@link runnerDisplayName}. */
export const RUNNER_NAME_SEPARATOR = '.';

/** What joins a runner's host to its name in {@link runnerDisplayName}. */
export const RUNNER_HOST_SEPARATOR = '/';

/**
 * `('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude', 'atlas')` → `atlas/r-claude`; with no host, `R-ABF3.r-claude`; with
 * no name, `compactRef(id)` in its place.
 */
export function runnerDisplayName(runnerId: string, runnerName?: string | null, hostName?: string | null): string {
  const ref = compactRef(runnerId);
  if (hostName) return `${hostName}${RUNNER_HOST_SEPARATOR}${runnerName || ref}`;
  return runnerName ? `${ref}${RUNNER_NAME_SEPARATOR}${runnerName}` : ref;
}

/**
 * A runner as a board card or row names it — `host/name`, or the name alone where the host is unknown — with
 * the compact id only for a runner that carries no name. The two sides stay apart so a view can clip each on
 * its own; the id stays reachable through {@link runnerTitle}.
 */
export interface RunnerBoardParts {
  /** The host, or `null` where it is unknown. */
  readonly host: string | null;
  readonly name: string;
}

export function runnerBoardParts(runnerId: string, runnerName?: string | null, hostName?: string | null): RunnerBoardParts {
  if (!runnerName) return { host: null, name: runnerDisplayName(runnerId, runnerName, hostName) };
  return { host: hostName || null, name: runnerName };
}

/** A runner reference's tooltip — the display name a surface may clip, then the full id:
 * `atlas/r-claude · rn_01KXKVVF1J3D6H6VYZ3XYNABF3`. */
export function runnerTitle(runnerId: string, runnerName?: string | null, hostName?: string | null): string {
  return `${runnerDisplayName(runnerId, runnerName, hostName)} · ${runnerId}`;
}

/**
 * Each runner's host name by runner id — what {@link runnerDisplayName} takes its host from. A neutral seam: the hub
 * app provides it from its host registry, and an app with no registry (the runner's) provides nothing, so its runners
 * keep the compact-id form.
 */
export const RUNNER_HOST_NAMES = new InjectionToken<Signal<ReadonlyMap<string, string>>>('fleet.RUNNER_HOST_NAMES', {
  providedIn: 'root',
  factory: () => signal<ReadonlyMap<string, string>>(new Map()).asReadonly(),
});

/** Each runner's host name by runner id now — {@link RUNNER_HOST_NAMES}'s value. */
export function injectRunnerHostNames(): Signal<ReadonlyMap<string, string>> {
  return inject(RUNNER_HOST_NAMES);
}
