import { type ChunkChanged, type DecisionEvent, type EventLoggedEvent, type FlowEvent, type HostEvent, type KeyedEvent, type QuestionEvent, type RunnerEvent } from './hub-events-wire';
import { ASK_CHANGE_CAUSES, ENVIRONMENT_CHANGE_CAUSES, ESCALATION_CHANGE_CAUSES, LEASE_CHANGE_CAUSES, TAKEOVER_CHANGE_CAUSES, type AskChanged, type EnvironmentChanged, type EscalationChanged, type FactChanged, type LeaseChanged, type TakeoverChanged, type RunnerEventType } from './runner-events-wire';

type RequiredKeys<T> = { [K in keyof T]-?: undefined extends T[K] ? never : K }[keyof T];
type OptionalKeys<T> = { [K in keyof T]-?: undefined extends T[K] ? K : never }[keyof T];

/** A per-kind field descriptor: `required`/`optional` are objects whose own keys must
 * be *exactly* `T`'s required/optional keys — renaming or adding a field on the
 * interface turns the object literal below red at compile time (a missing key). Dropping
 * one does too, *unless* the drop leaves that slot's key set empty: `Record<never, true>`
 * is `{}`, and TypeScript does not excess-property-check a literal against an object type
 * with no properties to compare against, so a stale key then compiles clean. Both slots
 * are therefore authored through `exactRequired`/`exactOptional` below, which close that
 * gap; `queue-changed` is the live case, with no fields of its own in either slot. */
interface FrameFieldSpec<T> {
  readonly required: Record<RequiredKeys<T>, true>;
  readonly optional: Record<OptionalKeys<T>, true>;
}

/** `Actual`'s key set must be exactly `Expected`'s, or this collapses to `never` — unlike
 * `Actual extends Expected` alone, it still catches an excess key when `Expected` has none
 * (`keyof {}` is `never`, so a plain structural check has nothing to compare against). */
type Exact<Expected, Actual> = Actual extends Expected
  ? Exclude<keyof Actual, keyof Expected> extends never
    ? Actual
    : never
  : never;

/** Authors a spec's `required` object so a stale-after-drop or missing-after-add key is a
 * compile error even when `RequiredKeys<T>` is `never` — see `FrameFieldSpec`'s doc. */
function exactRequired<T>() {
  return <O extends Record<RequiredKeys<T>, true>>(
    obj: Exact<Record<RequiredKeys<T>, true>, O>,
  ): Record<RequiredKeys<T>, true> => obj as Record<RequiredKeys<T>, true>;
}

/** The same exactness for a spec's `optional` object, over `OptionalKeys<T>`. */
function exactOptional<T>() {
  return <O extends Record<OptionalKeys<T>, true>>(
    obj: Exact<Record<OptionalKeys<T>, true>, O>,
  ): Record<OptionalKeys<T>, true> => obj as Record<OptionalKeys<T>, true>;
}

type ChunkChangedFrame = ChunkChanged & KeyedEvent;
type QuestionFrame = QuestionEvent & KeyedEvent;
type DecisionFrame = DecisionEvent & KeyedEvent;
type QueueChangedFrame = Record<never, never>;
type RunnerFrame = RunnerEvent & KeyedEvent;
type HostFrame = HostEvent & KeyedEvent;
/** A flow fact's frame names no fact identity: the record is read through the flow. */
type FlowFrame = FlowEvent;
type EventLoggedFrame = EventLoggedEvent & KeyedEvent;

interface HubFrameFieldSpecs {
  'chunk-changed': FrameFieldSpec<ChunkChangedFrame>;
  'question-asked': FrameFieldSpec<QuestionFrame>;
  'question-answered': FrameFieldSpec<QuestionFrame>;
  'decision-opened': FrameFieldSpec<DecisionFrame>;
  'decision-resolved': FrameFieldSpec<DecisionFrame>;
  'queue-changed': FrameFieldSpec<QueueChangedFrame>;
  'runner-changed': FrameFieldSpec<RunnerFrame>;
  'host-changed': FrameFieldSpec<HostFrame>;
  'flow-changed': FrameFieldSpec<FlowFrame>;
  'event-logged': FrameFieldSpec<EventLoggedFrame>;
}

export const HUB_FRAME_FIELD_SPECS: HubFrameFieldSpecs = {
  'chunk-changed': {
    required: exactRequired<ChunkChangedFrame>()({ chunk_id: true, status: true, terminal: true }),
    optional: exactOptional<ChunkChangedFrame>()({
      prev_status: true,
      prev_node: true,
      transition_from: true,
      transition_to: true,
      node: true,
      runner_id: true,
      runner_name: true,
      cause: true,
      graph_id: true,
      by: true,
      key: true,
      reason: true,
      project_id: true,
    }),
  },
  'question-asked': {
    required: exactRequired<QuestionFrame>()({ chunk_id: true, question_id: true }),
    optional: exactOptional<QuestionFrame>()({ key: true, project_id: true }),
  },
  'question-answered': {
    required: exactRequired<QuestionFrame>()({ chunk_id: true, question_id: true }),
    optional: exactOptional<QuestionFrame>()({ key: true, project_id: true }),
  },
  'decision-opened': {
    required: exactRequired<DecisionFrame>()({ chunk_id: true, decision_id: true }),
    optional: exactOptional<DecisionFrame>()({ key: true, project_id: true }),
  },
  'decision-resolved': {
    required: exactRequired<DecisionFrame>()({ chunk_id: true, decision_id: true }),
    optional: exactOptional<DecisionFrame>()({ key: true, project_id: true }),
  },
  'queue-changed': {
    required: exactRequired<QueueChangedFrame>()({}),
    optional: exactOptional<QueueChangedFrame>()({}),
  },
  'runner-changed': {
    required: exactRequired<RunnerFrame>()({ runner_id: true, kind: true }),
    optional: exactOptional<RunnerFrame>()({ runner_name: true, by: true, reason: true, key: true }),
  },
  'host-changed': {
    required: exactRequired<HostFrame>()({ host_id: true, kind: true }),
    optional: exactOptional<HostFrame>()({ host_name: true, by: true, key: true }),
  },
  'flow-changed': {
    required: exactRequired<FlowFrame>()({ flow_id: true, kind: true, submission_id: true, chunk_id: true }),
    optional: exactOptional<FlowFrame>()({ outcome: true }),
  },
  'event-logged': {
    required: exactRequired<EventLoggedFrame>()({
      severity: true,
      kind: true,
      chunk_id: true,
      runner_id: true,
    }),
    optional: exactOptional<EventLoggedFrame>()({ runner_name: true, key: true, project_id: true }),
  },
};

interface RunnerFrameFieldSpecs {
  'lease-changed': FrameFieldSpec<LeaseChanged>;
  'ask-changed': FrameFieldSpec<AskChanged>;
  'escalation-changed': FrameFieldSpec<EscalationChanged>;
  'takeover-changed': FrameFieldSpec<TakeoverChanged>;
  'environment-changed': FrameFieldSpec<EnvironmentChanged>;
  'fact-changed': FrameFieldSpec<FactChanged>;
}

export const RUNNER_FRAME_FIELD_SPECS: RunnerFrameFieldSpecs = {
  'lease-changed': {
    required: exactRequired<LeaseChanged>()({ runner_id: true, lease_id: true, chunk_id: true, cause: true }),
    optional: exactOptional<LeaseChanged>()({}),
  },
  'ask-changed': {
    required: exactRequired<AskChanged>()({ runner_id: true, lease_id: true, chunk_id: true, question_id: true, cause: true }),
    optional: exactOptional<AskChanged>()({}),
  },
  'escalation-changed': {
    required: exactRequired<EscalationChanged>()({ runner_id: true, chunk_id: true, cause: true }),
    optional: exactOptional<EscalationChanged>()({ lease_id: true }),
  },
  'takeover-changed': {
    required: exactRequired<TakeoverChanged>()({ runner_id: true, chunk_id: true, takeover_id: true, cause: true }),
    optional: exactOptional<TakeoverChanged>()({}),
  },
  'environment-changed': {
    required: exactRequired<EnvironmentChanged>()({ chunk_id: true, environment_id: true, cause: true }),
    optional: exactOptional<EnvironmentChanged>()({}),
  },
  'fact-changed': {
    required: exactRequired<FactChanged>()({ runner_id: true, seq: true, kind: true, chunk_id: true, lease_id: true }),
    optional: exactOptional<FactChanged>()({}),
  },
};

/** Every runner kind's `cause` union, by name — keyed on {@link RunnerEventType} with an
 * explicit `null` for a kind that carries no `cause`, so a seventh kind is a compile error
 * here rather than a row that silently goes unchecked.
 *
 * Cases of these kinds are named for the `cause` they carry, and the runtime check below
 * pins that: several kinds' cases are key-set-identical to each other (`lease-changed`,
 * `ask-changed`, `takeover-changed`, `environment-changed`), so a case's name is the only
 * thing separating `spawned` from `created` — and a name the body does not observe is the
 * vacuous claim `bzh:case-pins-its-own-name` is about. The `Object.keys`-only check above
 * cannot see it, and neither can union membership alone, since both values are in the
 * union. */
export const RUNNER_CAUSE_VALUES: Readonly<Record<RunnerEventType, readonly string[] | null>> = {
  'lease-changed': LEASE_CHANGE_CAUSES,
  'ask-changed': ASK_CHANGE_CAUSES,
  'escalation-changed': ESCALATION_CHANGE_CAUSES,
  'takeover-changed': TAKEOVER_CHANGE_CAUSES,
  'environment-changed': ENVIRONMENT_CHANGE_CAUSES,
  'fact-changed': null,
};
