import { openLookup, type runnerApi, type Tone } from 'fleet';

/** The host's registration as the host page shows it — every value resolved for display. */
export interface HostRegistrationSummary {
  readonly state: runnerApi.HostRegistrationView['state'];
  readonly label: string;
  readonly tone: Tone;
  /** One line per fact the state carries: identity, held runners, detail, buffered facts. */
  readonly lines: readonly string[];
  /** The hub's own sentence on the state — why it refused, or what to upgrade — `null` when it gave none. */
  readonly detail: string | null;
  /** When the hub last answered the host's report, `null` before any answer. */
  readonly answeredAt: string | null;
}

/** Each registration state's label and tone; a state this build does not know reads raw, in `idle`. */
const LABEL = openLookup<runnerApi.HostRegistrationView['state'], readonly [string, Tone]>(
  {
    unregistered: ['unregistered', 'idle'],
    pending: ['pending', 'waiting'],
    registered: ['registered', 'running'],
    refused: ['refused', 'needs'],
    no_hosts: ['hub without hosts', 'waiting'],
    unreachable: ['unreachable', 'stale'],
    version_refused: ['version refused', 'needs'],
  },
  (raw) => [raw, 'idle'],
);

/** Folds the host's registration read into its display summary — `null` before the read lands. */
export function hostRegistrationSummary(view: runnerApi.HostRegistrationView | undefined): HostRegistrationSummary | null {
  if (!view) return null;
  const [label, tone] = LABEL(view.state);
  const lines: string[] = [];
  if (view.state === 'unregistered') {
    lines.push('this host holds no host token — each runner signs in and reports on its own');
  } else if (view.state === 'pending') {
    lines.push('holds a host token, not yet reported to its hub');
  }
  if (view.host_id) lines.push(`host ${view.host_id}`);
  if (view.tenant_id) lines.push(`tenant ${view.tenant_id}`);
  if (view.host_name) lines.push(`named ${view.host_name}`);
  if (view.state === 'registered') {
    const held = view.held_runner_ids?.length ?? 0;
    lines.push(`${held} runner${held === 1 ? '' : 's'} held`);
  }
  if (view.detail) lines.push(view.detail);
  if (view.state === 'no_hosts') {
    const buffered = view.buffered_facts ?? 0;
    lines.push(`${buffered} fact${buffered === 1 ? '' : 's'} buffered`);
  }
  return { state: view.state, label, tone, lines, detail: view.detail ?? null, answeredAt: view.answered_at ?? null };
}
