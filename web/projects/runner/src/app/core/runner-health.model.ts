import { type BeaconTone, compactRef, type runnerApi, type Tone, unlisted } from 'fleet';

/** Why the hub will not take a runner's work, from its identity verdict, worded once for every surface that shows it. */
export interface IdentityRefusal {
  /** The badge word on the host row. */
  readonly label: string;
  /** The sentence on the hub panel's issue line. */
  readonly issue: string;
  /** The remedy or the hub's reason, for a hover; `null` when there is none. */
  readonly title: string | null;
  /** `needs` for a refusal this build knows; `idle` for a state it does not — read raw, never an alarm. */
  readonly tone: Tone;
}

/** The identity verdict's refusal, `null` while it checks out or goes unchecked. A state this build does not know
 * reads raw, in `idle` (`bzh:frontend-open-vocabulary`). */
export function identityRefusal(identity: runnerApi.IdentityVerdictView | null | undefined): IdentityRefusal | null {
  if (!identity) return null;
  switch (identity.state) {
    case 'unchecked':
    case 'matched':
      return null;
    case 'mismatched':
      return {
        label: 'REFUSING',
        issue: `refusing — its token belongs to ${compactRef(identity.token_runner_id ?? '')}`,
        title: identity.reset_command,
        tone: 'needs',
      };
    case 'refused':
      return {
        label: 'REFUSED',
        issue: 'the hub refuses its token',
        title: [identity.reason, identity.reset_command].filter(Boolean).join(' — ') || null,
        tone: 'needs',
      };
    case 'not_held':
      return { label: 'NOT HELD', issue: 'halted — not held by its host', title: identity.reason ?? null, tone: 'needs' };
    case 'tenant_differs':
      return { label: 'TENANT DIFFERS', issue: "halted — tenant differs from its host's", title: identity.reason ?? null, tone: 'needs' };
    case 'version_refused':
      return { label: 'VERSION REFUSED', issue: 'the hub refuses its version', title: identity.reason ?? null, tone: 'needs' };
    default: {
      const raw = unlisted(identity.state);
      return { label: raw, issue: raw, title: identity.reason ?? null, tone: 'idle' };
    }
  }
}

/** One runner's light, read the same on every surface: red for a halted runner, an unreachable hub, or a known
 * identity refusal; amber while either brake holds it; else green. `status` is the runner's own read, `undefined`
 * until it lands, when its pause falls back to the host's report. */
export function runnerLight(runner: runnerApi.HostRunnerView, status: runnerApi.RunnerStatusView | undefined): BeaconTone {
  if (runner.state !== 'serving') return 'red';
  if (status && !status.hub.reachable) return 'red';
  if (identityRefusal(status?.identity)?.tone === 'needs') return 'red';
  const pause = status?.pause ?? runner.pause;
  return pause?.local || pause?.hub ? 'amber' : 'green';
}

/** The runners' hub link folded to one badge: all reachable is `done`, none is `stale`, some is `waiting`. */
export function hubLink(reachable: number, total: number): { readonly label: string; readonly tone: Tone } {
  if (reachable >= total) return { label: 'CONNECTED', tone: 'done' };
  if (reachable === 0) return { label: 'UNREACHABLE', tone: 'stale' };
  return { label: `${reachable} / ${total} CONNECTED`, tone: 'waiting' };
}
