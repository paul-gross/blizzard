import type { FleetSelection } from './fleet-page.model';

/** What the probe needs of the selected host. */
export interface ProbeTarget {
  readonly hostId: string;
  readonly urls: readonly string[];
  readonly url: string | null;
}

/** What one `/api/health` probe of a declared origin answered. `legacy` is a reachable runner that
 * predates `host_id` in its health body — it cannot be verified, only reached. */
export type OriginProbe =
  | { readonly origin: string; readonly kind: 'unreachable' }
  | { readonly origin: string; readonly kind: 'legacy' }
  | { readonly origin: string; readonly kind: 'host'; readonly hostId: string | null };

/** How the host's URL fact renders. `plain` is today's unprobed link (no probe ran, or a legacy
 * runner answered); `verified` links an origin whose health named this host. */
export type HostOrigin =
  | { readonly kind: 'pending' }
  | { readonly kind: 'verified'; readonly href: string }
  | { readonly kind: 'plain'; readonly href: string }
  | { readonly kind: 'unreachable' }
  | { readonly kind: 'none' };

/** The probe's target for a selection: the open host's id and origins, `null` for anything else. */
export function hostProbeTarget(selection: FleetSelection | null): ProbeTarget | null {
  if (selection?.kind !== 'host') return null;
  const { hostId, urls, url } = selection.card;
  return { hostId, urls, url };
}

/** The URL fact before any probe is wired: the host's canonical `url` as a plain link. */
export function plainOrigin(url: string | null): HostOrigin {
  return url === null ? { kind: 'none' } : { kind: 'plain', href: url };
}

/**
 * The origin to link, from the probes in `urls` declaration order: the earliest whose answer names
 * `hostId`; else, when any origin gave a legacy answer, the plain `url` link; else `unreachable`.
 * No origins and no `url` leaves the fact empty.
 */
export function selectOrigin(probes: readonly OriginProbe[], hostId: string, url: string | null): HostOrigin {
  if (probes.length === 0) return plainOrigin(url);
  const verified = probes.find((probe) => probe.kind === 'host' && probe.hostId === hostId);
  if (verified) return { kind: 'verified', href: verified.origin };
  if (probes.some((probe) => probe.kind === 'legacy')) return plainOrigin(url ?? probes.find((p) => p.kind === 'legacy')!.origin);
  return { kind: 'unreachable' };
}
