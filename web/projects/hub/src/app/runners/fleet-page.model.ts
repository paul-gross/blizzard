import { type KitFact, type RunnerConnection, compactRef, formatAbsolute } from 'fleet';

import { busyAgents, declaredAgents, type HeldChunk, type HostCard, type HostGroups } from './host-rows.model';
import { type ConnectionLight, hostLight, runnerLight } from './connection-status.model';
import type { RunnerRow } from './runner-rows.model';

/** One card on the Fleet tab's list — a host, or a runner standing under no host. */
export interface FleetListCard {
  /** The host id or the runner id: one id space, so the URL's `:id` names either. */
  readonly id: string;
  readonly name: string;
  readonly online: boolean;
  readonly connection: RunnerConnection;
  /** The card's status light — a host's, or a host-less runner's with its pause. */
  readonly light: ConnectionLight;
  /** Agents declared across the card's bindings; `null` where none are declared. */
  readonly agents: number | null;
  /** The short ref shown right-aligned on the card's second line. */
  readonly ref: string;
}

/** The list pane: host cards in the hosts read's order, then the hostless runners. */
export interface FleetList {
  readonly hosts: readonly FleetListCard[];
  readonly hostless: readonly FleetListCard[];
}

/** The list pane's cards folded from the grouped rows. */
export function fleetList(groups: HostGroups<RunnerRow>): FleetList {
  return {
    hosts: groups.cards.map(({ card, rows }) => ({
      id: card.hostId,
      name: card.hostName,
      online: card.online,
      connection: card.connection,
      light: hostLight(card.online, card.connection),
      agents: declaredAgents(rows),
      ref: compactRef(card.hostId),
    })),
    hostless: groups.hostless.map((row) => ({
      id: row.runner_id,
      name: row.runner_name,
      online: row.online,
      connection: row.connection,
      light: runnerLight(row),
      agents: declaredAgents([row]),
      ref: compactRef(row.runner_id),
    })),
  };
}

/** A row under a workspace's heading: a chunk holding environments, or a free slot. */
export type EnvironmentLine =
  | { readonly kind: 'held'; readonly chunk: HeldChunk }
  | { readonly kind: 'free' };

/** One workspace of a host: its in-use count over its capacity, and the lines beneath. */
export interface WorkspaceLines {
  readonly workspaceId: string;
  readonly capacity: number | null;
  readonly inUse: number;
  readonly lines: readonly EnvironmentLine[];
}

/** The host's workspaces, each with its held chunks and the remaining capacity as free
 * lines. The hub carries no environment ids, so lines are keyed by chunk. */
export function workspaceLines(card: HostCard): readonly WorkspaceLines[] {
  return card.workspaces.flatMap((ws) => {
    if (ws.workspaceId === null) return [];
    const held = card.heldChunks.get(ws.workspaceId) ?? [];
    const free = ws.capacity === null ? 0 : Math.max(0, ws.capacity - ws.inUse);
    return [
      {
        workspaceId: ws.workspaceId,
        capacity: ws.capacity,
        inUse: ws.inUse,
        lines: [
          ...held.map((chunk): EnvironmentLine => ({ kind: 'held', chunk })),
          ...Array.from({ length: free }, (): EnvironmentLine => ({ kind: 'free' })),
        ],
      },
    ];
  });
}

/** What the right pane renders for a host. */
export interface HostDetailVm {
  readonly kind: 'host';
  readonly card: HostCard;
  readonly ref: string;
  readonly rows: readonly RunnerRow[];
  readonly agentsDeclared: number | null;
  readonly agentsBusy: number;
  /** The host's record facts: when it registered and who added it. */
  readonly facts: readonly KitFact[];
  readonly workspaces: readonly WorkspaceLines[];
  /** Environments in use across the host's workspaces, over their summed capacity; the
   * capacity is `null` while any workspace reports none. */
  readonly envsInUse: number;
  readonly envsCapacity: number | null;
}

/** What the right pane renders for a runner standing under no host. */
export interface RunnerDetailVm {
  readonly kind: 'runner';
  readonly row: RunnerRow;
  readonly ref: string;
  /** The Workspace section's facts: the workspace it runs in, and its record. */
  readonly facts: readonly KitFact[];
}

export type FleetSelection = HostDetailVm | RunnerDetailVm | { readonly kind: 'not-found'; readonly id: string };

const dash = (value: string | null): string => value ?? '—';

const addedLabel = (addedAt: string, addedBy: string | null): string =>
  addedBy === null ? formatAbsolute(addedAt) : `${formatAbsolute(addedAt)} by ${addedBy}`;

/** The host's record facts — when it first registered and who added it. */
function connectionFacts(card: HostCard): readonly KitFact[] {
  return [
    {
      label: 'Registered',
      value: card.registeredAt === null ? 'never' : formatAbsolute(card.registeredAt),
      testid: 'fleet-fact-registered',
    },
    { label: 'Added', value: addedLabel(card.addedAt, card.addedBy), testid: 'fleet-fact-added' },
  ];
}

/** A hostless runner's Workspace section: the one workspace it runs in, and its record. */
function runnerFacts(row: RunnerRow): readonly KitFact[] {
  return [
    { label: 'Workspace', value: dash(row.workspace_id ?? null), testid: 'fleet-fact-workspace' },
    {
      label: 'Registered',
      value: row.registered_at ? formatAbsolute(row.registered_at) : 'never',
      testid: 'fleet-fact-registered',
    },
    { label: 'Added', value: addedLabel(row.added_at, row.added_by ?? null), testid: 'fleet-fact-added' },
  ];
}

/** Resolves the URL's `:id` to a host, a hostless runner, or not-found; `null` for none. */
export function resolveSelection(groups: HostGroups<RunnerRow>, id: string | null): FleetSelection | null {
  if (id === null) return null;
  const host = groups.cards.find(({ card }) => card.hostId === id);
  if (host) {
    const workspaces = workspaceLines(host.card);
    return {
      kind: 'host',
      card: host.card,
      ref: compactRef(host.card.hostId),
      rows: host.rows,
      agentsDeclared: declaredAgents(host.rows),
      agentsBusy: busyAgents(host.rows),
      facts: connectionFacts(host.card),
      workspaces,
      envsInUse: workspaces.reduce((sum, ws) => sum + ws.inUse, 0),
      envsCapacity: workspaces.some((ws) => ws.capacity === null)
        ? null
        : workspaces.reduce((sum, ws) => sum + (ws.capacity ?? 0), 0),
    };
  }
  const row = groups.hostless.find((candidate) => candidate.runner_id === id);
  if (row) return { kind: 'runner', row, ref: compactRef(row.runner_id), facts: runnerFacts(row) };
  return { kind: 'not-found', id };
}

/** The id a bare `/fleet` opens: the first host card, else the first hostless runner. */
export function firstFleetId(list: FleetList): string | null {
  return list.hosts[0]?.id ?? list.hostless[0]?.id ?? null;
}
