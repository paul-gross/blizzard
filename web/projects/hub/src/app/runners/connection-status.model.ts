import { type BeaconBlink, type BeaconTone, RunnerConnection, type Tone } from 'fleet';

import { localPauseHint, type RunnerRow } from './runner-rows.model';

/** A status light: a hollow mark for one never connected, else a lit beacon. */
export type ConnectionLight = { readonly kind: 'never' } | { readonly kind: 'lit'; readonly tone: BeaconTone; readonly blink: BeaconBlink };

/** One badge of a runner's State fact: its label, tone, and the title or test hook it carries. */
export interface StateBadge {
  readonly label: string;
  readonly tone: Tone;
  readonly title?: string;
  readonly testid?: string;
}

/**
 * The one status-light ladder: a hollow mark when never connected, blinking red when offline — the
 * worst news outranks a pause — steady amber while paused, else steady green. A host carries no pause.
 */
function light(online: boolean, connection: RunnerConnection, paused: boolean): ConnectionLight {
  if (connection === RunnerConnection.NEVER_CONNECTED) return { kind: 'never' };
  if (!online) return { kind: 'lit', tone: 'red', blink: 'step' };
  if (paused) return { kind: 'lit', tone: 'amber', blink: 'hold' };
  return { kind: 'lit', tone: 'green', blink: 'hold' };
}

/** A runner's status light: paused when either the hub or the runner itself holds it. */
export function runnerLight(row: Pick<RunnerRow, 'online' | 'connection' | 'hub_paused' | 'locally_paused'>): ConnectionLight {
  return light(row.online, row.connection, (row.hub_paused ?? false) || (row.locally_paused ?? false));
}

/** A host's status light — the runner ladder with no pause. */
export function hostLight(online: boolean, connection: RunnerConnection): ConnectionLight {
  return light(online, connection, false);
}

/** A host's or runner's connection as a detail title bar states it, in its tone. */
export function connectionState(online: boolean, connection: RunnerConnection): { readonly label: string; readonly tone: Tone } {
  if (online) return { label: 'ONLINE', tone: 'done' };
  return connection === RunnerConnection.NEVER_CONNECTED
    ? { label: 'NEVER CONNECTED', tone: 'idle' }
    : { label: 'OFFLINE', tone: 'stale' };
}

/** A runner's connection as its detail title bar states it: a brake on an online runner outranks ONLINE, matching its light. */
export function runnerTitleState(row: RunnerRow): { readonly label: string; readonly tone: Tone } {
  if (row.online && row.hub_paused) return { label: 'HUB PAUSED', tone: 'waiting' };
  if (row.online && row.locally_paused) return { label: 'LOCALLY PAUSED', tone: 'idle' };
  return connectionState(row.online, row.connection);
}

/** A runner's State fact, stacked: its connection when not online, each brake that holds it, else WORKING or IDLE. */
export function runnerStateBadges(row: RunnerRow): readonly StateBadge[] {
  const badges: StateBadge[] = [];
  if (!row.online) {
    const { label, tone } = connectionState(row.online, row.connection);
    badges.push({ label, tone });
  }
  if (row.locally_paused) badges.push({ label: 'LOCALLY PAUSED', tone: 'idle', title: localPauseHint(row) });
  if (row.hub_paused) badges.push({ label: 'HUB PAUSED', tone: 'waiting', testid: 'fleet-runner-hub-paused' });
  if (row.online && !row.hub_paused && !row.locally_paused) {
    badges.push(row.claims.length > 0 ? { label: 'WORKING', tone: 'running' } : { label: 'IDLE', tone: 'idle' });
  }
  return badges;
}
