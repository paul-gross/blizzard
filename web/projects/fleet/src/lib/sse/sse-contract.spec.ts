import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import {
  HUB_EVENT_STREAM_URL,
  HUB_EVENT_TYPES,
  type ChunkChanged,
  type DecisionEvent,
  type EventLoggedEvent,
  type HubEventPayload,
  type KeyedEvent,
  type QuestionEvent,
  type RunnerEvent,
} from './fleet-live';
import { SseService, type SseEvent } from './sse.service';

import chunkChangedGolden from '../../../../../../contracts/sse/chunk-changed.json';
import decisionOpenedGolden from '../../../../../../contracts/sse/decision-opened.json';
import decisionResolvedGolden from '../../../../../../contracts/sse/decision-resolved.json';
import eventLoggedGolden from '../../../../../../contracts/sse/event-logged.json';
import manifestJson from '../../../../../../contracts/sse/manifest.json';
import queueChangedGolden from '../../../../../../contracts/sse/queue-changed.json';
import questionAnsweredGolden from '../../../../../../contracts/sse/question-answered.json';
import questionAskedGolden from '../../../../../../contracts/sse/question-asked.json';
import runnerChangedGolden from '../../../../../../contracts/sse/runner-changed.json';

/**
 * The SSE frame shape contract — the board half (issue #235). `contracts/sse/` is the
 * single description of every frame kind's wire shape; this spec and the Python suite
 * at `tests/test_sse_contract.py` read the same physical files. Moving a golden
 * reddens whichever side has not caught up; changing this side's expectation without
 * moving the golden reddens this side.
 *
 * Two claims, both proven against the real code rather than a hand-built stand-in:
 *
 * - **Type satisfaction** ({@link FRAME_FIELD_SPECS}): every key named in a spec is a
 *   `keyof` its kind's real interface (a compile error the moment a field is renamed
 *   or deleted there), and every one of that interface's non-optional fields must
 *   appear in the spec's `required` object (`Record<RequiredKeys<T>, true>` — an
 *   object type with exactly those keys, so a missing or extra key is also a compile
 *   error). The runtime half below then checks each golden's own key set against it.
 * - **Runtime parse** (the `describes the real transport` spec): every golden, framed
 *   exactly as the hub frames it, is fed through the real {@link SseService} /
 *   `FetchEventSource` byte-stream reader via a stubbed `fetch`, and the spec asserts
 *   on what reaches {@link SseHandle.events} — not on a hand-parsed object.
 */

interface Manifest {
  readonly kinds: readonly string[];
  readonly reserved_comment: string;
  readonly keepalive_comment: string;
  readonly frame_line_order: readonly string[];
}
const manifest = manifestJson as Manifest;

type GoldenCases = Readonly<Record<string, Readonly<Record<string, unknown>>>>;
const GOLDENS: Readonly<Record<string, GoldenCases>> = {
  'chunk-changed': chunkChangedGolden as GoldenCases,
  'question-asked': questionAskedGolden as GoldenCases,
  'question-answered': questionAnsweredGolden as GoldenCases,
  'decision-opened': decisionOpenedGolden as GoldenCases,
  'decision-resolved': decisionResolvedGolden as GoldenCases,
  'queue-changed': queueChangedGolden as GoldenCases,
  'runner-changed': runnerChangedGolden as GoldenCases,
  'event-logged': eventLoggedGolden as GoldenCases,
};

/** Every `(kind, caseName, payload)` triple in the corpus, in manifest/on-disk order —
 * the same traversal order the Python suite's `_cases()` uses. */
const CASES: readonly { kind: string; caseName: string; payload: Readonly<Record<string, unknown>> }[] =
  manifest.kinds.flatMap((kind) =>
    Object.entries(GOLDENS[kind]).map(([caseName, payload]) => ({ kind, caseName, payload })),
  );

// ---- FRAME_FIELD_SPECS: the type-satisfaction foothold ---------------------------

type RequiredKeys<T> = { [K in keyof T]-?: undefined extends T[K] ? never : K }[keyof T];
type OptionalKeys<T> = { [K in keyof T]-?: undefined extends T[K] ? K : never }[keyof T];

/** A per-kind field descriptor: `required`/`optional` are objects whose own keys must
 * be *exactly* `T`'s required/optional keys — renaming, adding, or dropping a field on
 * the interface changes `RequiredKeys<T>`/`OptionalKeys<T>`, which turns the object
 * literal below red at compile time (a missing or excess property). */
interface FrameFieldSpec<T> {
  readonly required: Record<RequiredKeys<T>, true>;
  readonly optional: Record<OptionalKeys<T>, true>;
}

type ChunkChangedFrame = ChunkChanged & KeyedEvent;
type QuestionFrame = QuestionEvent & KeyedEvent;
type DecisionFrame = DecisionEvent & KeyedEvent;
type QueueChangedFrame = Record<never, never>;
type RunnerFrame = RunnerEvent & KeyedEvent;
type EventLoggedFrame = EventLoggedEvent & KeyedEvent;

interface FrameFieldSpecs {
  'chunk-changed': FrameFieldSpec<ChunkChangedFrame>;
  'question-asked': FrameFieldSpec<QuestionFrame>;
  'question-answered': FrameFieldSpec<QuestionFrame>;
  'decision-opened': FrameFieldSpec<DecisionFrame>;
  'decision-resolved': FrameFieldSpec<DecisionFrame>;
  'queue-changed': FrameFieldSpec<QueueChangedFrame>;
  'runner-changed': FrameFieldSpec<RunnerFrame>;
  'event-logged': FrameFieldSpec<EventLoggedFrame>;
}

const FRAME_FIELD_SPECS: FrameFieldSpecs = {
  'chunk-changed': {
    required: { chunk_id: true, status: true },
    optional: { prev_status: true, prev_node: true, node: true, runner_id: true, cause: true, graph_id: true, key: true },
  },
  'question-asked': {
    required: { chunk_id: true, question_id: true },
    optional: { key: true },
  },
  'question-answered': {
    required: { chunk_id: true, question_id: true },
    optional: { key: true },
  },
  'decision-opened': {
    required: { chunk_id: true, decision_id: true },
    optional: { key: true },
  },
  'decision-resolved': {
    required: { chunk_id: true, decision_id: true },
    optional: { key: true },
  },
  'queue-changed': {
    required: {},
    optional: {},
  },
  'runner-changed': {
    required: { runner_id: true, kind: true },
    optional: { by: true, reason: true, key: true },
  },
  'event-logged': {
    required: { severity: true, kind: true, chunk_id: true, runner_id: true },
    optional: { key: true },
  },
};

function describedKeys(spec: FrameFieldSpec<object>): { required: string[]; all: Set<string> } {
  const required = Object.keys(spec.required);
  const optional = Object.keys(spec.optional);
  return { required, all: new Set([...required, ...optional]) };
}

// ---- The real transport, driven with a stubbed `fetch` ---------------------------

function renderFrame(id: number, kind: string, payload: unknown): string {
  return `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify(payload)}\n\n`;
}

function buildFrameStreamText(): string {
  const parts: string[] = [manifest.reserved_comment];
  let id = 0;
  for (const { kind, payload } of CASES) {
    id += 1;
    parts.push(renderFrame(id, kind, payload));
    // Interleave a keepalive partway through — it must never surface as an event.
    if (id === 3) parts.push(manifest.keepalive_comment);
  }
  return parts.join('');
}

function textStream(text: string): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes);
      controller.close();
    },
  });
}

describe('SSE frame shape contract', () => {
  it('manifest kind list deep-equals HUB_EVENT_TYPES', () => {
    expect(manifest.kinds).toEqual([...HUB_EVENT_TYPES]);
  });

  describe('type satisfaction (FRAME_FIELD_SPECS)', () => {
    for (const { kind, caseName, payload } of CASES) {
      it(`${kind}:${caseName} — key set matches its interface's required/optional split`, () => {
        const spec = FRAME_FIELD_SPECS[kind as keyof FrameFieldSpecs];
        const { required, all } = describedKeys(spec);
        const payloadKeys = Object.keys(payload);
        for (const key of payloadKeys) {
          expect(all.has(key), `${kind}:${caseName} carries undeclared field "${key}"`).toBe(true);
        }
        for (const key of required) {
          expect(payloadKeys, `${kind}:${caseName} is missing required field "${key}"`).toContain(key);
        }
      });
    }
  });

  describe('the real transport (stubbed fetch, real FetchEventSource byte reader)', () => {
    beforeEach(() => {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
          status: 200,
          ok: true,
          body: textStream(buildFrameStreamText()),
        } as unknown as Response),
      );
      TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it('delivers every golden case parsed, in order, with comments never surfacing as events', async () => {
      const handle = TestBed.inject(SseService).connect<HubEventPayload>(HUB_EVENT_STREAM_URL, {
        events: [...HUB_EVENT_TYPES],
      });
      const received: SseEvent<HubEventPayload>[] = [];
      const messages: HubEventPayload[] = [];
      handle.events.subscribe((event) => received.push(event));
      handle.messages.subscribe((message) => messages.push(message));

      await vi.waitFor(() => expect(received).toHaveLength(CASES.length));

      expect(received.map((event) => ({ type: event.type, data: event.data }))).toEqual(
        CASES.map(({ kind, payload }) => ({ type: kind, data: payload })),
      );
      // The reserved comment and the interleaved keepalive carry no `data:` line, so
      // the real byte reader must never surface either as a message or a named event.
      expect(messages).toEqual([]);

      handle.close();
    });
  });
});
