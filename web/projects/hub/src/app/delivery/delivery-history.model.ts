import {
  type ChunkDetail,
  type FlowFactView,
  type FlowView,
  formatAbsolute,
  formatSeenTag,
  formatWhen,
  type hubApi,
  type RepositorySummary,
  shortCommitHash,
  type Tone,
} from 'fleet';

import { ordinal, placeStateLabel, placeStateTone } from '../board/flows/flow-place.model';

/** The graph nodes whose visits the history reads as delivery: the push, the delivery itself, and the resolve loop. */
const DELIVERY_NODES = /deliver|pre-push|resolve/i;

/** The lines of a failed step's output the history shows before its expander. */
export const OUTPUT_TAIL_LINES = 12;

/** A link the drawer draws. */
export interface DrawerLink {
  readonly text: string;
  readonly url: string | null;
}

/** One repository the chunk delivers to, as the drawer's repo block draws it. */
export interface DrawerRepo {
  /** `blizzard`. */
  readonly name: string;
  readonly forge: string;
  readonly state: string;
  readonly tone: Tone;
  readonly prs: readonly DrawerLink[];
  readonly branch: string | null;
  readonly commit: DrawerLink | null;
  /** The last settled attempt on the repo's flow: its outcome and age, or `null` for none. */
  readonly lastAttempt: { readonly outcome: string; readonly age: string; readonly title: string } | null;
}

/** A failed step a bounce names. */
export interface DrawerStep {
  readonly name: string;
  readonly exitCode: number | null;
  readonly outputTail: string | null;
  /** Whether the tail runs past {@link OUTPUT_TAIL_LINES}, so it folds. */
  readonly long: boolean;
}

/** One entry of the delivery history. */
export interface HistoryEntry {
  readonly id: string;
  readonly at: string;
  /** The short stamp — `13:25`, `Yesterday 23:52`, a date. */
  readonly when: string;
  readonly whenTitle: string;
  readonly age: string;
  /** The repositories it happened on, `blizzard, blizzard-context`, or `null` for a chunk-wide entry. */
  readonly repos: string | null;
  readonly text: string;
  readonly tone: Tone;
  /** A bounce's full account. */
  readonly detail: string | null;
  readonly step: DrawerStep | null;
  /** The key of the bounce's log artifact, or `null`. */
  readonly logKey: string | null;
}

export interface DrawerSummary {
  readonly attempts: number;
  readonly bounces: number;
  /** From the first submission to the landing, or to now: `[1h 53m]`; `null` before any submission. */
  readonly span: string | null;
  readonly spanLanded: boolean;
  readonly reposLanded: number;
  readonly reposTotal: number;
}

export interface DeliveryDrawerView {
  readonly repos: readonly DrawerRepo[];
  readonly history: readonly HistoryEntry[];
  readonly summary: DrawerSummary;
}

/** A bounce envelope's fields the drawer reads. */
interface Envelope {
  readonly detail: string | null;
  readonly step: { readonly name: string; readonly exit_code?: number | null; readonly output_tail?: string | null } | null;
  readonly unlanded: readonly { readonly repo: string; readonly branch?: string; readonly pr?: { readonly number: number; readonly url: string } }[];
  readonly logArtifact: string | null;
}

function parseEnvelope(raw: string): Envelope {
  try {
    const parsed = JSON.parse(raw) as {
      detail?: string | null;
      step?: Envelope['step'];
      unlanded?: Envelope['unlanded'];
      action?: { log_artifact?: string | null } | null;
    };
    return {
      detail: parsed.detail || null,
      step: parsed.step && typeof parsed.step.name === 'string' ? parsed.step : null,
      unlanded: Array.isArray(parsed.unlanded) ? parsed.unlanded : [],
      logArtifact: parsed.action?.log_artifact ?? null,
    };
  } catch {
    return { detail: null, step: null, unlanded: [], logArtifact: null };
  }
}

function shortName(forge: string): string {
  return forge.split('/').pop() ?? forge;
}

/** Each flow's forge-side `owner/repo`, by flow id. */
function flowForges(flows: readonly FlowView[], repositories: readonly RepositorySummary[]): ReadonlyMap<string, string> {
  return new Map(
    flows.flatMap((flow) => {
      const repository = repositories.find((each) => each.repository_id === flow.repository_id);
      return repository ? [[flow.flow_id, `${repository.owner}/${repository.repo}`] as const] : [];
    }),
  );
}

function stamp(at: string, now: number): Pick<HistoryEntry, 'at' | 'when' | 'whenTitle' | 'age'> {
  return { at, when: formatWhen(at, new Date(now)), whenTitle: formatAbsolute(at), age: formatSeenTag(at, now) };
}

function outcomeTone(outcome: string | null): Tone {
  if (outcome === 'landed') return 'done';
  if (outcome === 'conflict') return 'waiting';
  if (outcome === 'pending' || outcome === null) return 'idle';
  return 'stale';
}

/** `HH:MM` of an instant. */
function clock(at: string): string {
  return formatWhen(at, new Date(at));
}

interface Draft extends Omit<HistoryEntry, 'repos'> {
  readonly repos: readonly string[];
}

function draft(id: string, at: string, now: number, repo: string | null, text: string, tone: Tone): Draft {
  return { id, ...stamp(at, now), repos: repo ? [repo] : [], text, tone, detail: null, step: null, logKey: null };
}

/**
 * One flow's facts as history entries, oldest first. A run of polls — a turn started and ended `pending`, again and
 * again — folds into one `waiting on checks` entry; a turn that settles reads as its outcome, the turn's start folded
 * in; a withdrawal right after the turn that caused it joins that entry.
 */
function flowEntries(facts: readonly FlowFactView[], repo: string | null, now: number): Draft[] {
  const out: Draft[] = [];
  let polls: FlowFactView[] = [];
  const flushPolls = (): void => {
    const first = polls[0];
    const last = polls[polls.length - 1];
    if (first && last) {
      const count = polls.filter((fact) => fact.kind === 'turn-ended').length;
      const span = clock(first.at) === clock(last.at) ? clock(first.at) : `${clock(first.at)}–${clock(last.at)}`;
      out.push({ ...draft(`poll-${first.id}`, last.at, now, repo, `waiting on checks · ${count} poll${count === 1 ? '' : 's'} · ${span}`, 'idle') });
    }
    polls = [];
  };
  facts.forEach((fact, index) => {
    const next = facts[index + 1];
    if (fact.kind === 'turn-started') {
      if (next?.kind === 'turn-ended' && next.outcome !== 'pending') return;
      if (next?.kind === 'turn-ended' || polls.length > 0) {
        polls.push(fact);
        return;
      }
      flushPolls();
      out.push(draft(`f-${fact.id}`, fact.at, now, repo, 'landing attempt started', 'running'));
      return;
    }
    if (fact.kind === 'turn-ended' && fact.outcome === 'pending') {
      polls.push(fact);
      return;
    }
    flushPolls();
    if (fact.kind === 'submitted') out.push(draft(`f-${fact.id}`, fact.at, now, repo, 'queued', 'spawning'));
    else if (fact.kind === 'landed') out.push(draft(`f-${fact.id}`, fact.at, now, repo, fact.revision ? `landed ${shortCommitHash(fact.revision)}` : 'landed', 'done'));
    else if (fact.kind === 'turn-ended' && fact.outcome === 'landed') return;
    else if (fact.kind === 'turn-ended') {
      const withdrawn = next?.kind === 'withdrawn' && next.submission_id === fact.submission_id;
      out.push(draft(`f-${fact.id}`, fact.at, now, repo, `landing attempt ${fact.outcome}${withdrawn ? ' · withdrawn' : ''}`, outcomeTone(fact.outcome)));
    } else if (fact.kind === 'withdrawn') {
      const prev = facts[index - 1];
      if (prev?.kind === 'turn-ended' && prev.submission_id === fact.submission_id && prev.outcome !== 'landed') return;
      if (fact.outcome === 'landed') return;
      out.push(draft(`f-${fact.id}`, fact.at, now, repo, `withdrawn${fact.outcome ? ` (${fact.outcome})` : ''}`, 'idle'));
    } else out.push(draft(`f-${fact.id}`, fact.at, now, repo, fact.kind, 'idle'));
  });
  flushPolls();
  return out;
}

/** Entries the same thing said on several flows at the same moment — a two-repo chunk's submission, its shared
 * turn's outcome — merge into one, naming every repository. */
function mergeAcrossFlows(drafts: readonly Draft[]): Draft[] {
  const merged = new Map<string, Draft>();
  for (const entry of drafts) {
    const key = `${entry.text.replace(/ · \d+ polls?.*$/, '')}@${entry.at.slice(0, 19)}`;
    const held = merged.get(key);
    merged.set(key, held ? { ...held, repos: [...held.repos, ...entry.repos] } : entry);
  }
  return [...merged.values()];
}

/** The log artifact a bounce names: the newest artifact of that name recorded no later than the bounce. */
function logKey(detail: ChunkDetail, name: string | null, at: string): string | null {
  if (name === null) return null;
  const candidates = detail.artifacts.filter((artifact) => artifact.name === name && (artifact.recorded_at ?? '') <= at).sort((a, b) => (b.recorded_at ?? '').localeCompare(a.recorded_at ?? ''));
  return candidates[0]?.key ?? null;
}

function bounceEntry(detail: ChunkDetail, bounce: hubApi.BounceView, index: number, now: number): Draft {
  const envelope = parseEnvelope(bounce.envelope);
  const step = envelope.step;
  const tail = step?.output_tail ?? null;
  return {
    ...draft(`b-${index}`, bounce.recorded_at, now, null, `bounced · ${bounce.cause}`, outcomeTone(bounce.cause)),
    repos: envelope.unlanded.map((each) => shortName(each.repo)),
    detail: envelope.detail,
    step: step ? { name: step.name, exitCode: step.exit_code ?? null, outputTail: tail, long: (tail?.split('\n').length ?? 0) > OUTPUT_TAIL_LINES } : null,
    logKey: logKey(detail, envelope.logArtifact, bounce.recorded_at),
  };
}

function transitionText(transition: hubApi.TransitionView): string {
  const choice = transition.choice_name ? ` (${transition.choice_name})` : '';
  if (transition.to_node_name && /resolve/i.test(transition.to_node_name)) return `sent back to ${transition.to_node_name}${choice}`;
  return `${transition.from_node_name ?? 'start'} → ${transition.to_node_name ?? 'end'}${choice}`;
}

function transitionTone(transition: hubApi.TransitionView): Tone {
  if (transition.to_node_name && /resolve/i.test(transition.to_node_name)) return 'waiting';
  return 'running';
}

/** The chunk's delivery history, newest first: its facts on every flow, its bounces, and its visits to the delivery
 * nodes. */
export function deliveryHistory(detail: ChunkDetail, forges: ReadonlyMap<string, string>, now: number): readonly HistoryEntry[] {
  const byFlow = new Map<string, FlowFactView[]>();
  for (const fact of [...detail.flow_record].sort((a, b) => a.at.localeCompare(b.at) || a.id - b.id)) {
    byFlow.set(fact.flow_id, [...(byFlow.get(fact.flow_id) ?? []), fact]);
  }
  const flowDrafts = mergeAcrossFlows(
    [...byFlow.entries()].flatMap(([flowId, facts]) => {
      const forge = forges.get(flowId);
      return flowEntries(facts, forge ? shortName(forge) : null, now);
    }),
  );
  const transitions = detail.history
    .filter((transition) => DELIVERY_NODES.test(transition.from_node_name ?? '') || DELIVERY_NODES.test(transition.to_node_name ?? ''))
    .map((transition, index) => draft(`t-${index}`, transition.recorded_at, now, null, transitionText(transition), transitionTone(transition)));
  const bounces = detail.bounces.map((bounce, index) => bounceEntry(detail, bounce, index, now));
  return [...flowDrafts, ...transitions, ...bounces]
    .sort((a, b) => b.at.localeCompare(a.at))
    .map((entry) => ({ ...entry, repos: entry.repos.length ? [...new Set(entry.repos)].join(', ') : null }));
}

/** Every repository the chunk touches, by forge-side `owner/repo`, in first-seen order. */
function chunkForges(detail: ChunkDetail, forges: ReadonlyMap<string, string>): readonly string[] {
  const seen = [
    ...detail.flow_record.flatMap((fact) => forges.get(fact.flow_id) ?? []),
    ...(detail.flow_places ?? []).flatMap((place) => forges.get(place.flow_id) ?? []),
    ...(detail.open_prs ?? []).map((pr) => pr.repo),
    ...(detail.closed_prs ?? []).map((pr) => pr.repo),
    ...(detail.landed_repos ?? []).map((landed) => landed.repo),
  ];
  return [...new Set(seen)];
}

function repoBlock(detail: ChunkDetail, forge: string, forges: ReadonlyMap<string, string>, now: number): DrawerRepo {
  const flowIds = new Set([...forges.entries()].filter(([, each]) => each === forge).map(([flowId]) => flowId));
  const landed = (detail.landed_repos ?? []).find((each) => each.repo === forge);
  const place = (detail.flow_places ?? []).find((each) => flowIds.has(each.flow_id));
  const settled = detail.flow_record
    .filter((fact) => flowIds.has(fact.flow_id) && fact.kind === 'turn-ended' && fact.outcome !== null && fact.outcome !== 'pending')
    .sort((a, b) => b.at.localeCompare(a.at))[0];
  const openPrs = (detail.open_prs ?? []).filter((pr) => pr.repo === forge);
  const closedPrs = (detail.closed_prs ?? []).filter((pr) => pr.repo === forge);
  const unlanded = detail.bounces.flatMap((bounce) => parseEnvelope(bounce.envelope).unlanded).filter((each) => each.repo === forge);
  const committed = detail.artifacts
    .filter((artifact) => artifact.kind === 'git_commit' && artifact.name === forge && artifact.branch_name)
    .sort((a, b) => (b.recorded_at ?? '').localeCompare(a.recorded_at ?? ''))[0];

  let state: string;
  let tone: Tone;
  if (landed) [state, tone] = ['landed', 'done'];
  else if (place) [state, tone] = [place.state === 'landing' || place.state === 'awaiting-merge' || place.state === 'passed-over' ? placeStateLabel(place.state) : `in line · ${ordinal(place.position)}`, placeStateTone(place.state)];
  else if (settled && settled.outcome !== 'landed') [state, tone] = [`bounced · ${settled.outcome}`, outcomeTone(settled.outcome)];
  else if (openPrs.length) [state, tone] = [detail.awaiting_external_merge ? 'awaiting merge' : 'PR open', 'idle'];
  else [state, tone] = ['not submitted', 'idle'];

  return {
    name: shortName(forge),
    forge,
    state,
    tone,
    prs: [...openPrs, ...closedPrs].map((pr) => ({ text: `#${pr.number}`, url: pr.url })),
    branch: unlanded[unlanded.length - 1]?.branch ?? committed?.branch_name ?? null,
    commit: landed ? { text: shortCommitHash(landed.commit_hash), url: landed.url } : null,
    lastAttempt: settled ? { outcome: settled.outcome ?? '', age: formatSeenTag(settled.at, now), title: formatAbsolute(settled.at) } : null,
  };
}

function summary(detail: ChunkDetail, repos: readonly DrawerRepo[], now: number): DrawerSummary {
  const submitted = detail.flow_record.filter((fact) => fact.kind === 'submitted').sort((a, b) => a.at.localeCompare(b.at));
  const landedAt = detail.flow_record.filter((fact) => fact.kind === 'landed').sort((a, b) => b.at.localeCompare(a.at))[0]?.at ?? null;
  const first = submitted[0]?.at ?? null;
  const landedAll = repos.length > 0 && repos.every((repo) => repo.state === 'landed');
  const end = landedAll && landedAt ? Date.parse(landedAt) : now;
  return {
    attempts: new Set(submitted.map((fact) => fact.submission_id)).size,
    bounces: detail.bounces.length,
    span: first ? formatSeenTag(first, end) : null,
    spanLanded: landedAll && landedAt !== null,
    reposLanded: repos.filter((repo) => repo.state === 'landed').length,
    reposTotal: repos.length,
  };
}

/** The drawer's whole view of one chunk — `null` while its detail is unread: a block per repository, the delivery history, and a summary. */
export function deliveryDrawerView(
  detail: ChunkDetail | undefined,
  flows: readonly FlowView[],
  repositories: readonly RepositorySummary[],
  now: number,
): DeliveryDrawerView | null {
  if (detail === undefined) return null;
  const forges = flowForges(flows, repositories);
  const repos = chunkForges(detail, forges).map((forge) => repoBlock(detail, forge, forges, now));
  return { repos, history: deliveryHistory(detail, forges, now), summary: summary(detail, repos, now) };
}

/** A tail's first {@link OUTPUT_TAIL_LINES} lines, or all of it when unfolded. */
export function outputTail(step: DrawerStep, unfolded: boolean): string {
  if (!step.outputTail) return '';
  return unfolded || !step.long ? step.outputTail : step.outputTail.split('\n').slice(-OUTPUT_TAIL_LINES).join('\n');
}
