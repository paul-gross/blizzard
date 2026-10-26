import { TextDecoder } from 'node:util';
import { SESSION_COOKIE, sessionOf } from './auth';
import type { Ledger } from './client/ledger';
import type { Person } from './people';
import type { Json } from './client/clients';

/** One frame of the stream, as `contracts/sse/` defines them: an `id`, an `event` kind, and a JSON payload. */
export interface Frame {
  id: number;
  event: string;
  data: Json;
}

/** Parses the frames in a chunk of stream text; a comment frame (reserved open or keepalive) carries no `data:` and is dropped. */
export function parseFrames(text: string): { frames: Frame[]; rest: string } {
  const blocks = text.split('\n\n');
  const rest = blocks.pop() ?? '';
  const frames: Frame[] = [];
  for (const block of blocks) {
    let id: number | undefined;
    let event = 'message';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
      if (field === 'id') id = Number(value);
      else if (field === 'event') event = value;
      else if (field === 'data') data.push(value);
    }
    if (data.length === 0 || id === undefined) continue;
    frames.push({ id, event, data: JSON.parse(data.join('\n')) });
  }
  return { frames, rest };
}

export interface WaitOptions {
  timeout?: number;
}

export interface Subscription {
  /** Every frame admitted so far, in order. */
  readonly frames: readonly Frame[];
  /** The `content-type` the first connection answered with; a reconnect does not replace it. */
  readonly contentType: string;
  /** The comment line the first connection opened with, as the hub sent it; a reconnect does not replace it. */
  readonly openingComment: string;
  /** Resolves with the first matching frame, past or to come; its timeout is a failure, never a wait that ends quietly. */
  waitFor(predicate: (frame: Frame) => boolean, options?: WaitOptions): Promise<Frame>;
  /**
   * Resolves once every predicate has matched, with one frame per predicate in predicate order. Each predicate takes
   * its own first matching frame, so one frame may satisfy two of them, as two `waitFor` calls would. At its timeout it
   * fails naming each predicate still unmet and the frames seen.
   */
  waitForAll(predicates: ((frame: Frame) => boolean)[], options?: WaitOptions): Promise<Frame[]>;
  close(): void;
}

/** A subscription with a barrier chunk of its own, opened with `open({ barrier: true })`. */
export interface BarrierSubscription extends Subscription {
  /** The barrier chunk: the test's own, in its ledger and its chunk lists, so a whole-list assertion can leave it out. */
  readonly barrierChunkId: string;
  /**
   * Edits the barrier chunk and waits for the `chunk-changed` that edit publishes. The stream is ordered, so every frame
   * an earlier act caused has arrived by then. Returns the frames admitted since the previous barrier, or since the
   * subscription began; none names the barrier chunk, here or in `frames`.
   */
  barrier(): Promise<Frame[]>;
}

export interface EventStream {
  /**
   * Subscribes before it returns, so a test opens it before the act it waits on. With `as`, the subscription carries that
   * person's session instead of the tenant admin's.
   */
  open(options?: { as?: Person }): Promise<Subscription>;
  /**
   * Files and ingests a barrier chunk first, then subscribes from now at every level, so the barrier's own ingest
   * frames are never the test's.
   */
  open(options: { barrier: true; as?: Person }): Promise<BarrierSubscription>;
}

export interface Events extends EventStream {
  /** The whole stream, not narrowed to what the test made. */
  readonly unfiltered: EventStream;
}

const DEFAULT_WAIT_MS = 15_000;
const RECONNECT_DELAY_MS = 100;

/** The barrier chunk a reader waits on, and the act that makes it publish. */
interface Barrier {
  chunkId: string;
  /** Edits the barrier chunk, with a value no earlier edit used. */
  edit: () => Promise<void>;
}

interface Reader {
  url: string;
  headers: Record<string, string>;
  /** Whether a frame is passed on. */
  admits: (frame: Frame) => boolean;
  /** Whether the first connect joins from now (`from=latest`) rather than with no cursor. A resume never does. */
  fromNow?: boolean;
  barrier?: Barrier;
}

const namesChunk = (frame: Frame, chunkId: string): boolean => Object.values(frame.data ?? {}).some((value) => value === chunkId);

class StreamReader implements BarrierSubscription {
  /** Every frame the stream delivered; `frames` is what the test may see of them. */
  private readonly delivered: Frame[] = [];
  private lastId = 0;
  private closed = false;
  private abort = new AbortController();
  private readonly waiters = new Set<() => void>();
  private failure: Error | undefined;
  private connected = false;
  contentType = '';
  openingComment = '';
  /** Where in `delivered` the window the next barrier answers for begins. */
  private windowStart = 0;

  constructor(private readonly reader: Reader) {}

  get barrierChunkId(): string {
    return this.reader.barrier?.chunkId ?? '';
  }

  /** Whether the test may see the frame: the reader admits it, and it does not name the barrier chunk. */
  private visible(frame: Frame): boolean {
    const barrier = this.reader.barrier;
    return this.reader.admits(frame) && (barrier === undefined || !namesChunk(frame, barrier.chunkId));
  }

  /**
   * The frames the reader passes on. A frame can arrive before the request that caused it returns, so whether it names
   * an id the ledger holds is decided when it is read, not when it arrives.
   */
  get frames(): Frame[] {
    return this.delivered.filter((frame) => this.visible(frame));
  }

  /** Connects and returns once the stream is open and the hub has subscribed us. */
  async start(): Promise<void> {
    await this.connect();
  }

  private async connect(): Promise<void> {
    this.abort = new AbortController();
    const headers: Record<string, string> = { ...this.reader.headers, accept: 'text/event-stream' };
    // A resume carries only `Last-Event-ID`: the hub refuses a `from` beside it.
    let url = this.reader.url;
    if (this.lastId > 0) headers['Last-Event-ID'] = String(this.lastId);
    else if (this.reader.fromNow === true) url += `${url.includes('?') ? '&' : '?'}from=latest`;
    const response = await fetch(url, { headers, signal: this.abort.signal });
    if (!response.ok || response.body === null) {
      throw new Error(`GET ${url} answered ${response.status}: ${await response.text()}`);
    }
    const body = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    // The first chunk of the body is the reserved open-of-stream comment, sent once the subscription is in place.
    const opening = await body.read();
    if (!opening.done) buffer += decoder.decode(opening.value, { stream: true });
    if (!this.connected) {
      this.connected = true;
      this.contentType = response.headers.get('content-type') ?? '';
      // The first read can carry frames behind the comment; the comment is the first line.
      this.openingComment = buffer.split('\n')[0] ?? '';
    }
    void this.pump(body, decoder, buffer);
  }

  private async pump(body: ReadableStreamDefaultReader<Uint8Array>, decoder: TextDecoder, initial: string): Promise<void> {
    let buffer = initial;
    try {
      for (;;) {
        const parsed = parseFrames(buffer);
        buffer = parsed.rest;
        for (const frame of parsed.frames) {
          // A frame at or below the last id seen was already passed on before the connection dropped.
          if (frame.id <= this.lastId) continue;
          this.lastId = frame.id;
          this.delivered.push(frame);
        }
        this.wake();
        const chunk = await body.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
      }
    } catch {
      // A dropped connection resumes below, unless the test closed it.
    }
    if (this.closed) return;
    if (this.reader.fromNow === true && this.lastId === 0) {
      // Rejoining from now would skip whatever the hub published while the connection was down.
      this.failure = new Error('the stream joined from now dropped before any frame arrived, so it has no cursor to resume from and the frames in the gap are unrecoverable');
      this.wake();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_DELAY_MS));
    try {
      if (!this.closed) await this.connect();
    } catch (error) {
      this.failure = error as Error;
      this.wake();
    }
  }

  private wake(): void {
    for (const waiter of [...this.waiters]) waiter();
  }

  /** Resolves with what `look` finds, looking on each frame and on a short interval; rejects at the timeout with `unmet()`. */
  private until<T>(look: () => T | undefined, timeout: number, unmet: () => string): Promise<T> {
    return new Promise((resolve, reject) => {
      // The ledger can grow without a frame arriving, so a wait looks again on a short interval as well as on each frame.
      const poll = setInterval(() => check(), 50);
      const timer = setTimeout(() => {
        finish();
        reject(new Error(`${unmet()} within ${timeout}ms; saw ${this.frames.map((frame) => `${frame.id}:${frame.event}`).join(', ') || 'none'}`));
      }, timeout);
      const finish = (): void => {
        clearInterval(poll);
        clearTimeout(timer);
        this.waiters.delete(check);
      };
      const check = (): void => {
        if (this.failure !== undefined) {
          finish();
          reject(this.failure);
          return;
        }
        const found = look();
        if (found !== undefined) {
          finish();
          resolve(found);
        }
      };
      this.waiters.add(check);
      check();
    });
  }

  waitFor(predicate: (frame: Frame) => boolean, options: WaitOptions = {}): Promise<Frame> {
    return this.until(() => this.frames.find(predicate), options.timeout ?? DEFAULT_WAIT_MS, () => 'no frame matched');
  }

  waitForAll(predicates: ((frame: Frame) => boolean)[], options: WaitOptions = {}): Promise<Frame[]> {
    const look = (): (Frame | undefined)[] => {
      const frames = this.frames;
      return predicates.map((predicate) => frames.find(predicate));
    };
    return this.until(
      () => {
        const found = look();
        return found.every((frame) => frame !== undefined) ? (found as Frame[]) : undefined;
      },
      options.timeout ?? DEFAULT_WAIT_MS,
      () => {
        const unmet = look().flatMap((frame, index) => (frame === undefined ? [`#${index} ${predicates[index]?.toString()}`] : []));
        return `${unmet.length} of ${predicates.length} predicates unmatched (${unmet.join('; ')})`;
      },
    );
  }

  async barrier(): Promise<Frame[]> {
    const barrier = this.reader.barrier;
    if (barrier === undefined) throw new Error('this subscription was not opened with { barrier: true }');
    await barrier.edit();
    const edited = (frame: Frame): boolean => frame.event === 'chunk-changed' && frame.data?.chunk_id === barrier.chunkId && frame.data?.cause === 'edited';
    const at = await this.until(
      () => {
        const index = this.delivered.findIndex((frame, position) => position >= this.windowStart && edited(frame));
        return index === -1 ? undefined : index;
      },
      DEFAULT_WAIT_MS,
      () => `no chunk-changed (edited) for the barrier chunk ${barrier.chunkId} arrived`,
    );
    const answer = this.delivered.slice(this.windowStart, at).filter((frame) => this.visible(frame));
    this.windowStart = at + 1;
    return answer;
  }

  close(): void {
    this.closed = true;
    this.abort.abort();
  }
}

/**
 * The hub's event stream, read with Node's own streaming `fetch` because Playwright's request context reads a response
 * whole. At the levels that share a tenant, a frame is passed on only if it names an id the ledger holds, and the stream
 * joins from now, as a barrier subscription does at every level.
 */
export function eventStream(options: {
  hubUrl: string;
  tenantId: string;
  ledger: Ledger;
  narrow: boolean;
  /** Whether a subscription without a barrier joins from now. */
  fromNow: boolean;
  opened: Subscription[];
  /** The superuser's `bz_session` on a hub with authentication on: what a subscription carries unless it is opened `as` a person. */
  superuserSession: () => Promise<string | undefined>;
  /** Files the barrier chunk, and edits it with the value given. Reached only by `open({ barrier: true })`. */
  barrier: { file: () => Promise<string>; edit: (chunkId: string, effort: string) => Promise<void> };
}): Events {
  const stream = (narrow: boolean): EventStream => {
    const open = async (openOptions?: { barrier?: true; as?: Person }): Promise<StreamReader> => {
      const admits = (frame: Frame): boolean =>
        !narrow || Object.values(frame.data ?? {}).some((value) => options.ledger.holds(value));
      // The chunk is filed before the stream opens, so none of its ingest frames can reach the subscription.
      let barrier: Barrier | undefined;
      if (openOptions?.barrier === true) {
        const chunkId = await options.barrier.file();
        let edits = 0;
        barrier = { chunkId, edit: () => options.barrier.edit(chunkId, `barrier-${++edits}`) };
      }
      const subscription = new StreamReader({
        url: `${options.hubUrl}/api/events/stream`,
        headers: { 'X-Blizzard-Tenant': options.tenantId, ...(await sessionHeaders(openOptions?.as, options.superuserSession)) },
        admits,
        fromNow: barrier !== undefined || options.fromNow,
        barrier,
      });
      await subscription.start();
      options.opened.push(subscription);
      return subscription;
    };
    return { open: open as EventStream['open'] };
  };
  return { ...stream(options.narrow), unfiltered: stream(false) };
}

/** The cookie header a subscription carries: the person's session, else the superuser's; none on a hub with authentication off. */
async function sessionHeaders(person: Person | undefined, superuser: () => Promise<string | undefined>): Promise<Record<string, string>> {
  const session = person === undefined ? await superuser() : await sessionOf(person.context);
  if (session === undefined) {
    if (person !== undefined) throw new Error(`${person.email} holds no session to subscribe with`);
    return {};
  }
  return { cookie: `${SESSION_COOKIE}=${session}` };
}

/** A reader over any stream URL; the harness's own proof points it at a stub. */
export async function openStream(reader: Reader): Promise<Subscription> {
  const subscription = new StreamReader(reader);
  await subscription.start();
  return subscription;
}
