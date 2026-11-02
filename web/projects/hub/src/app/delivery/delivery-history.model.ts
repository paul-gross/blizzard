import {
  bounceReason,
  type ChunkDetail,
  type FlowFactView,
  type FlowView,
  formatAbsolute,
  formatSeenTag,
  formatWhen,
  formatWhenParts,
  type hubApi,
  parseBounceEnvelope,
  type RepositorySummary,
  shortCommitHash,
  type Tone,
} from 'fleet';

import { ordinal, placeStateLabel, placeStateTone } from '../core/flow-place.model';

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
  readonly lastAttempt: { readonly outcome: string; readonly tone: Tone; readonly age: string; readonly title: string } | null;
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
  /** The instant split for a two-line stamp: the day (empty today) over the `HH:MM`. */
  readonly whenParts: { readonly day: string; readonly time: string };
  readonly whenTitle: string;
  readonly age: string;
  /** The repositories it happened on, `blizzard · blizzard-context`, or `null` for a chunk-wide entry. */
  readonly repos: string | null;
  readonly text: string;
  /** {@link HistoryTone}: `null` reads in the default text colour. */
  readonly tone: HistoryTone;
  /** A bounce's full account. */
  readonly detail: string | null;
  readonly step: DrawerStep | null;
  /** The key of the bounce's log artifact, or `null`. */
  readonly logKey: string | null;
  /** Whether this is a graph transition stitched in beside a delivery event, drawn secondary to the events. */
  readonly transition: boolean;
  /** A transition's parts — the node it left, the choice that moved it, the node it reached; `null` for an event. */
  readonly move: { readonly graph: string | null; readonly from: string; readonly choice: string | null; readonly to: string } | null;
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

/** Whether a settled turn's submission was withdrawn right after it: the flow's next fact in record order (by `id`,
 * never by instant) is a withdrawal of the same submission. `facts` is one flow's facts in any order. */
export function withdrawnAfter(facts: readonly FlowFactView[], turn: FlowFactView): boolean {
  const next = facts.filter((fact) => fact.id > turn.id).sort((a, b) => a.id - b.id)[0];
  return next?.kind === 'withdrawn' && next.submission_id === turn.submission_id;
}

/** The repositories a bounce left unlanded, by forge-side `owner/repo` — none for an envelope that does not parse. */
function unlandedOf(envelope: hubApi.BounceEnvelope | null): readonly hubApi.BounceUnlandedRepo[] {
  return envelope?.unlanded ?? [];
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

function stamp(at: string, now: number): Pick<HistoryEntry, 'at' | 'when' | 'whenParts' | 'whenTitle' | 'age'> {
  return { at, when: formatWhen(at, new Date(now)), whenParts: formatWhenParts(at, new Date(now)), whenTitle: formatAbsolute(at), age: formatSeenTag(at, now) };
}

/** A history entry's colour, by meaning alone: success green (`done`), routine rework amber (`waiting`), a problem red
 * (`needs`), passive waiting and context grey (`idle`), and neutral progress the default text colour (`null`). */
export type HistoryTone = 'done' | 'waiting' | 'needs' | 'idle' | null;

/** The tone of a delivery event: a landing succeeds; a turn or bounce the hub marks as routine rework waits on the step
 * that takes it up; any other turn that settled without landing — sent back, or ended judging nothing — any other
 * bounce, and a withdrawal are problems; a run of waits and a transition are context; anything else is neutral
 * progress. */
function historyTone(kind: 'submitted' | 'started' | 'turn-ended' | 'landed' | 'bounced' | 'withdrawn' | 'polls' | 'transition', rework = false): HistoryTone {
  switch (kind) {
    case 'landed':
      return 'done';
    case 'bounced':
    case 'turn-ended':
      return rework ? 'waiting' : 'needs';
    case 'withdrawn':
      return 'needs';
    case 'polls':
    case 'transition':
      return 'idle';
    default:
      return null;
  }
}

/** A settled turn's tone: a landing done, routine rework waiting on the step that takes it up, any other verdict
 * stale. */
function verdictTone(turn: FlowFactView): Tone {
  if (turn.verdict === 'landed') return 'done';
  return turn.rework ? 'waiting' : 'stale';
}

/** `HH:MM` of an instant. */
function clock(at: string): string {
  return formatWhen(at, new Date(at));
}

interface Draft extends Omit<HistoryEntry, 'repos'> {
  readonly repos: readonly string[];
}

function draft(id: string, at: string, now: number, repo: string | null, text: string, tone: HistoryTone): Draft {
  return { id, ...stamp(at, now), repos: repo ? [repo] : [], text, tone, detail: null, step: null, logKey: null, transition: false, move: null };
}

/**
 * One flow's facts as history entries, oldest first. A run of waits — a turn started and ended only waiting, again and
 * again — folds into one `waiting on checks` entry; any other turn that settles reads as its outcome, the turn's start
 * folded in, a landing as the landed entry alone — or as its turn, when it had nothing to merge and wrote none; a withdrawal right after the turn entry it caused joins that entry,
 * and one right after a landing is the landing's own.
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
      out.push({ ...draft(`poll-${first.id}`, last.at, now, repo, `waiting on checks · ${count} poll${count === 1 ? '' : 's'} · ${span}`, historyTone('polls')) });
    }
    polls = [];
  };
  facts.forEach((fact, index) => {
    const next = facts[index + 1];
    if (fact.kind === 'turn-started') {
      if (next?.kind === 'turn-ended' && !next.wait) return;
      if (next?.kind === 'turn-ended' || polls.length > 0) {
        polls.push(fact);
        return;
      }
      flushPolls();
      out.push(draft(`f-${fact.id}`, fact.at, now, repo, 'landing attempt started', historyTone('started')));
      return;
    }
    if (fact.kind === 'turn-ended' && fact.wait) {
      polls.push(fact);
      return;
    }
    flushPolls();
    if (fact.kind === 'submitted') out.push(draft(`f-${fact.id}`, fact.at, now, repo, 'queued', historyTone('submitted')));
    else if (fact.kind === 'landed') out.push(draft(`f-${fact.id}`, fact.at, now, repo, fact.revision ? `landed ${shortCommitHash(fact.revision)}` : 'landed', historyTone('landed')));
    else if (fact.kind === 'turn-ended' && fact.verdict === 'landed') {
      // A landing with nothing to merge writes no `landed` fact; its turn speaks for it.
      if (!facts.some((each) => each.kind === 'landed' && each.submission_id === fact.submission_id)) out.push(draft(`f-${fact.id}`, fact.at, now, repo, 'landed · no changes', historyTone('landed')));
    } else if (fact.kind === 'turn-ended') {
      const withdrawn = withdrawnAfter(facts, fact);
      out.push(draft(`f-${fact.id}`, fact.at, now, repo, `landing attempt ${fact.outcome}${withdrawn ? ' · withdrawn' : ''}`, historyTone('turn-ended', fact.rework)));
    } else if (fact.kind === 'withdrawn') {
      const prev = facts[index - 1];
      if (prev?.submission_id === fact.submission_id && (prev.kind === 'landed' || (prev.kind === 'turn-ended' && !prev.wait))) return;
      out.push(draft(`f-${fact.id}`, fact.at, now, repo, `withdrawn${fact.outcome ? ` (${fact.outcome})` : ''}`, historyTone('withdrawn')));
    } else out.push(draft(`f-${fact.id}`, fact.at, now, repo, fact.kind, null));
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
  const envelope = parseBounceEnvelope(bounce.envelope);
  const step = envelope?.step ?? null;
  const tail = step?.output_tail ?? null;
  return {
    ...draft(`b-${index}`, bounce.recorded_at, now, null, `bounced · ${bounce.cause}`, historyTone('bounced', bounce.rework)),
    repos: unlandedOf(envelope).map((each) => shortName(each.repo)),
    detail: envelope?.detail ? bounceReason(bounce.envelope) : null,
    step: step ? { name: step.name, exitCode: step.exit_code, outputTail: tail, long: (tail?.split('\n').length ?? 0) > OUTPUT_TAIL_LINES } : null,
    logKey: logKey(detail, envelope?.action?.log_artifact ?? null, bounce.recorded_at),
  };
}

function transitionText(transition: hubApi.TransitionView): string {
  const choice = transition.choice_name ? ` · ${transition.choice_name}` : '';
  return `${transition.from_node_name ?? 'start'}${choice} → ${transition.to_node_name ?? 'end'}`;
}

/**
 * The graph transitions that frame the delivery events: for each anchor instant, the latest transition at or before it
 * and the earliest after it, each once — the chunk's route into and out of every delivery event, whatever its graph
 * names its nodes. `transitions` must be sorted by `recorded_at`.
 */
function framingTransitions(transitions: readonly hubApi.TransitionView[], anchors: readonly string[]): readonly hubApi.TransitionView[] {
  const picked = new Set<number>();
  for (const at of anchors) {
    const after = transitions.findIndex((transition) => transition.recorded_at > at);
    const before = (after === -1 ? transitions.length : after) - 1;
    if (before >= 0) picked.add(before);
    if (after !== -1) picked.add(after);
  }
  return [...picked].sort((a, b) => a - b).map((index) => transitions[index]);
}

/** The chunk's delivery history, newest first: its facts on every flow, its bounces, and the graph transitions
 * immediately before and after each of them. */
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
  const bounces = detail.bounces.map((bounce, index) => bounceEntry(detail, bounce, index, now));
  const sorted = [...detail.history].sort((a, b) => a.recorded_at.localeCompare(b.recorded_at));
  const transitions = framingTransitions(sorted, [...flowDrafts, ...bounces].map((entry) => entry.at)).map((transition, index) => ({
    ...draft(`t-${index}`, transition.recorded_at, now, null, transitionText(transition), historyTone('transition')),
    transition: true,
    move: { graph: transition.graph_name ?? null, from: transition.from_node_name ?? 'start', choice: transition.choice_name ?? null, to: transition.to_node_name ?? 'end' },
  }));
  return [...flowDrafts, ...transitions, ...bounces]
    .sort((a, b) => b.at.localeCompare(a.at))
    .map((entry) => ({ ...entry, repos: entry.repos.length ? [...new Set(entry.repos)].join(' · ') : null }));
}

/** Every repository the chunk touches, by forge-side `owner/repo`, in first-seen order. */
function chunkForges(detail: ChunkDetail, forges: ReadonlyMap<string, string>): readonly string[] {
  const seen = [
    ...detail.flow_record.flatMap((fact) => forges.get(fact.flow_id) ?? []),
    ...detail.flow_places.flatMap((place) => forges.get(place.flow_id) ?? []),
    ...detail.open_prs.map((pr) => pr.repo),
    ...detail.closed_prs.map((pr) => pr.repo),
    ...detail.landed_repos.map((landed) => landed.repo),
  ];
  return [...new Set(seen)];
}

function repoBlock(detail: ChunkDetail, forge: string, forges: ReadonlyMap<string, string>, now: number): DrawerRepo {
  const flowIds = new Set([...forges.entries()].filter(([, each]) => each === forge).map(([flowId]) => flowId));
  const landed = detail.landed_repos.find((each) => each.repo === forge);
  const place = detail.flow_places.find((each) => flowIds.has(each.flow_id));
  const settled = detail.flow_record.filter((fact) => flowIds.has(fact.flow_id) && fact.verdict !== null).sort((a, b) => b.at.localeCompare(a.at))[0];
  const openPrs = detail.open_prs.filter((pr) => pr.repo === forge);
  const closedPrs = detail.closed_prs.filter((pr) => pr.repo === forge);
  const unlanded = detail.bounces.flatMap((bounce) => unlandedOf(parseBounceEnvelope(bounce.envelope))).filter((each) => each.repo === forge);
  const committed = detail.artifacts
    .filter((artifact) => artifact.kind === 'git_commit' && artifact.name === forge && artifact.branch_name)
    .sort((a, b) => (b.recorded_at ?? '').localeCompare(a.recorded_at ?? ''))[0];

  let state: string;
  let tone: Tone;
  if (landed) [state, tone] = ['landed', 'done'];
  // A turn that landed with nothing to merge leaves no landed repository behind.
  else if (settled?.verdict === 'landed') [state, tone] = ['landed · no changes', 'done'];
  else if (place) [state, tone] = [place.state === 'landing' || place.state === 'awaiting-merge' || place.state === 'passed-over' ? placeStateLabel(place.state) : `in line · ${ordinal(place.position)}`, placeStateTone(place.state)];
  else if (settled) [state, tone] = [`bounced · ${settled.outcome}`, verdictTone(settled)];
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
    lastAttempt: settled ? { outcome: settled.outcome ?? '', tone: verdictTone(settled), age: formatSeenTag(settled.at, now), title: formatAbsolute(settled.at) } : null,
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
