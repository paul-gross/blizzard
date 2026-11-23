import { HUB_EVENT_STREAM_URL, HUB_EVENT_TYPES } from './hub-events';
import { RUNNER_EVENT_STREAM_URL, RUNNER_EVENT_TYPES } from './runner-events';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { HUB_FRAME_FIELD_SPECS, RUNNER_FRAME_FIELD_SPECS, RUNNER_CAUSE_VALUES } from '../testing/transport/sse-field-specs';
import { SseService, type SseEvent } from './sse.service';

import chunkChangedGolden from '../../../../../../contracts/sse/chunk-changed.json';
import decisionOpenedGolden from '../../../../../../contracts/sse/decision-opened.json';
import decisionResolvedGolden from '../../../../../../contracts/sse/decision-resolved.json';
import eventLoggedGolden from '../../../../../../contracts/sse/event-logged.json';
import manifestJson from '../../../../../../contracts/sse/manifest.json';
import queueChangedGolden from '../../../../../../contracts/sse/queue-changed.json';
import questionAnsweredGolden from '../../../../../../contracts/sse/question-answered.json';
import questionAskedGolden from '../../../../../../contracts/sse/question-asked.json';
import hostChangedGolden from '../../../../../../contracts/sse/host-changed.json';
import flowChangedGolden from '../../../../../../contracts/sse/flow-changed.json';
import runnerChangedGolden from '../../../../../../contracts/sse/runner-changed.json';
import runnerAskChangedGolden from '../../../../../../contracts/sse/runner/ask-changed.json';
import runnerEnvironmentChangedGolden from '../../../../../../contracts/sse/runner/environment-changed.json';
import runnerEscalationChangedGolden from '../../../../../../contracts/sse/runner/escalation-changed.json';
import runnerFactChangedGolden from '../../../../../../contracts/sse/runner/fact-changed.json';
import runnerLeaseChangedGolden from '../../../../../../contracts/sse/runner/lease-changed.json';
import runnerManifestJson from '../../../../../../contracts/sse/runner/manifest.json';
import runnerTakeoverChangedGolden from '../../../../../../contracts/sse/runner/takeover-changed.json';

/**
 * The SSE frame shape contract — the board half of the hub scope, and
 * the runner scope the runner app consumes.
 * `contracts/sse/` is the single description of every frame kind's wire shape, one
 * self-contained scope per daemon; this spec and the Python suite at
 * `tests/component/wire/test_sse_contract.py` read the same physical files. Moving a golden reddens
 * whichever side has not caught up; changing this side's expectation without moving the
 * golden reddens this side.
 *
 * The claims, all proven against the real code rather than a hand-built stand-in:
 *
 * - **Type satisfaction** (`FRAME_FIELD_SPECS`): every key named in a spec is a `keyof`
 *   its kind's real interface (a compile error the moment a field is renamed or deleted
 *   there), and every one of that interface's non-optional fields must appear in the
 *   spec's `required` object (`Record<RequiredKeys<T>, true>` — an object type with
 *   exactly those keys, so a missing or extra key is also a compile error). Both slots
 *   are authored through `exactRequired`/`exactOptional` (below), which also catch a
 *   stale key for a kind with no fields of its own in that slot — the plain
 *   `Record<…Keys<T>, true>` alone cannot, since it degrades to `{}` there. The runtime
 *   half below then checks each golden's own key set against it.
 * - **Cause naming** (runner scope only): each case of a kind that carries a `cause` is
 *   named for that cause, and the payload must actually carry it — the key-set check
 *   cannot separate two cases with identical keys, and the cases of four runner kinds
 *   are exactly that.
 * - **Runtime parse** (the `describes the real transport` spec): every golden, framed
 *   exactly as its daemon frames it, is fed through the real {@link SseService} /
 *   `FetchEventSource` byte-stream reader via a stubbed `fetch`, and the spec asserts on
 *   what reaches {@link SseHandle.events} — not on a hand-parsed object.
 */

interface Manifest {
  readonly kinds: readonly string[];
  readonly reserved_comment: string;
  readonly keepalive_comment: string;
  readonly frame_line_order: readonly string[];
}
const hubManifest = manifestJson as Manifest;
const runnerManifest = runnerManifestJson as Manifest;

type GoldenCases = Readonly<Record<string, Readonly<Record<string, unknown>>>>;
interface CorpusCase {
  kind: string;
  caseName: string;
  payload: Readonly<Record<string, unknown>>;
}

/** Every `(kind, caseName, payload)` triple in `manifest`'s scope, in manifest/on-disk
 * order — the same traversal order the Python suite's `_cases()` uses. */
function casesOf(manifest: Manifest, goldens: Readonly<Record<string, GoldenCases>>): readonly CorpusCase[] {
  return manifest.kinds.flatMap((kind) =>
    Object.entries(goldens[kind]).map(([caseName, payload]) => ({ kind, caseName, payload })),
  );
}

// ---- The hub scope -----------------------------------------------------------------

const HUB_GOLDENS: Readonly<Record<string, GoldenCases>> = {
  'chunk-changed': chunkChangedGolden as GoldenCases,
  'question-asked': questionAskedGolden as GoldenCases,
  'question-answered': questionAnsweredGolden as GoldenCases,
  'decision-opened': decisionOpenedGolden as GoldenCases,
  'decision-resolved': decisionResolvedGolden as GoldenCases,
  'queue-changed': queueChangedGolden as GoldenCases,
  'runner-changed': runnerChangedGolden as GoldenCases,
  'host-changed': hostChangedGolden as GoldenCases,
  'flow-changed': flowChangedGolden as GoldenCases,
  'event-logged': eventLoggedGolden as GoldenCases,
};
const HUB_CASES = casesOf(hubManifest, HUB_GOLDENS);

// ---- The runner scope ----------------------------------------------------------------

const RUNNER_GOLDENS: Readonly<Record<string, GoldenCases>> = {
  'lease-changed': runnerLeaseChangedGolden as GoldenCases,
  'ask-changed': runnerAskChangedGolden as GoldenCases,
  'escalation-changed': runnerEscalationChangedGolden as GoldenCases,
  'takeover-changed': runnerTakeoverChangedGolden as GoldenCases,
  'environment-changed': runnerEnvironmentChangedGolden as GoldenCases,
  'fact-changed': runnerFactChangedGolden as GoldenCases,
};
const RUNNER_CASES = casesOf(runnerManifest, RUNNER_GOLDENS);

// ---- Shared runtime machinery --------------------------------------------------------

function describedKeys(spec: { readonly required: object; readonly optional: object }): { required: string[]; all: Set<string> } {
  const required = Object.keys(spec.required);
  const optional = Object.keys(spec.optional);
  return { required, all: new Set([...required, ...optional]) };
}

function renderFrame(id: number, kind: string, payload: unknown): string {
  return `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify(payload)}\n\n`;
}

function buildFrameStreamText(manifest: Manifest, cases: readonly CorpusCase[]): string {
  const parts: string[] = [manifest.reserved_comment];
  let id = 0;
  for (const { kind, payload } of cases) {
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

/** Registers the two runtime describe blocks (type satisfaction + real transport) one
 * scope's worth of goldens drives. The real transport parses untrusted payloads.
 * `causeValuesOf`, when given, binds a case's name to the `cause` its payload carries —
 * the key-set check below cannot tell apart two cases with the same keys but a different
 * `cause`, and a case named for a cause it does not carry pins nothing. It returns `null`
 * for a kind with no `cause` at all (only the runner scope names its cases this way). */
function describeScopeContract(
  scopeName: string,
  streamUrl: string,
  eventTypes: readonly string[],
  manifest: Manifest,
  cases: readonly CorpusCase[],
  fieldSpecOf: (kind: string) => { readonly required: object; readonly optional: object },
  causeValuesOf?: (kind: string) => readonly string[] | null | undefined,
): void {
  describe(`${scopeName} scope`, () => {
    it('manifest kind list deep-equals the broker constants', () => {
      expect(manifest.kinds).toEqual([...eventTypes]);
    });

    describe('type satisfaction (FRAME_FIELD_SPECS)', () => {
      for (const { kind, caseName, payload } of cases) {
        it(`${kind}:${caseName} — key set matches its interface's required/optional split`, () => {
          const spec = fieldSpecOf(kind);
          const { required, all } = describedKeys(spec);
          const payloadKeys = Object.keys(payload);
          for (const key of payloadKeys) {
            expect(all.has(key), `${kind}:${caseName} carries undeclared field "${key}"`).toBe(true);
          }
          for (const key of required) {
            expect(payloadKeys, `${kind}:${caseName} is missing required field "${key}"`).toContain(key);
          }
        });

        const causeValues = causeValuesOf?.(kind);
        if (causeValues != null) {
          it(`${kind}:${caseName} — carries the cause its case name claims`, () => {
            expect(causeValues, `"${caseName}" is not one of ${kind}'s declared causes`).toContain(
              caseName,
            );
            expect(
              (payload as { cause?: unknown }).cause,
              `${kind}:${caseName} carries a cause its name does not claim`,
            ).toBe(caseName);
          });
        }
      }
    });

    describe('the real transport (stubbed fetch, real FetchEventSource byte reader)', () => {
      beforeEach(() => {
        vi.stubGlobal(
          'fetch',
          vi.fn().mockResolvedValue({
            status: 200,
            ok: true,
            body: textStream(buildFrameStreamText(manifest, cases)),
          } as unknown as Response),
        );
        TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
      });

      afterEach(() => {
        vi.unstubAllGlobals();
      });

      it('delivers every golden case parsed, in order, with comments never surfacing as events', async () => {
        const handle = TestBed.inject(SseService).connect<unknown>(streamUrl, {
          events: [...eventTypes],
        });
        const received: SseEvent<unknown>[] = [];
        const messages: unknown[] = [];
        handle.events.subscribe((event) => received.push(event));
        handle.messages.subscribe((message) => messages.push(message));

        await vi.waitFor(() => expect(received).toHaveLength(cases.length));

        expect(received.map((event) => ({ type: event.type, data: event.data }))).toEqual(
          cases.map(({ kind, payload }) => ({ type: kind, data: payload })),
        );
        // The reserved comment and the interleaved keepalive carry no `data:` line, so
        // the real byte reader must never surface either as a message or a named event.
        expect(messages).toEqual([]);

        handle.close();
      });
    });
  });
}

describe('SSE frame shape contract', () => {
  describeScopeContract(
    'hub',
    HUB_EVENT_STREAM_URL,
    HUB_EVENT_TYPES,
    hubManifest,
    HUB_CASES,
    (kind) => HUB_FRAME_FIELD_SPECS[kind as keyof typeof HUB_FRAME_FIELD_SPECS],
  );
  describeScopeContract(
    'runner',
    RUNNER_EVENT_STREAM_URL,
    RUNNER_EVENT_TYPES,
    runnerManifest,
    RUNNER_CASES,
    (kind) => RUNNER_FRAME_FIELD_SPECS[kind as keyof typeof RUNNER_FRAME_FIELD_SPECS],
    (kind) => RUNNER_CAUSE_VALUES[kind as keyof typeof RUNNER_CAUSE_VALUES],
  );
});
