import { TextDecoder } from 'node:util';
import type { Ledger } from './client/ledger';
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
  /** Resolves with the first matching frame, past or to come; its timeout is a failure, never a wait that ends quietly. */
  waitFor(predicate: (frame: Frame) => boolean, options?: WaitOptions): Promise<Frame>;
  close(): void;
}

export interface EventStream {
  /** Subscribes before it returns, so a test opens it before the act it waits on. */
  open(): Promise<Subscription>;
}

export interface Events extends EventStream {
  /** The whole stream, not narrowed to what the test made. */
  readonly unfiltered: EventStream;
}

const DEFAULT_WAIT_MS = 15_000;
const RECONNECT_DELAY_MS = 100;

interface Reader {
  url: string;
  headers: Record<string, string>;
  /** Whether a frame is passed on. */
  admits: (frame: Frame) => boolean;
}

class StreamReader implements Subscription {
  /** Every frame the stream delivered; `frames` is what the test may see of them. */
  private readonly delivered: Frame[] = [];
  private lastId = 0;
  private closed = false;
  private abort = new AbortController();
  private readonly waiters = new Set<() => void>();
  private failure: Error | undefined;

  constructor(private readonly reader: Reader) {}

  /**
   * The frames the reader passes on. A frame can arrive before the request that caused it returns, so whether it names
   * an id the ledger holds is decided when it is read, not when it arrives.
   */
  get frames(): Frame[] {
    return this.delivered.filter((frame) => this.reader.admits(frame));
  }

  /** Connects and returns once the stream is open and the hub has subscribed us. */
  async start(): Promise<void> {
    await this.connect();
  }

  private async connect(): Promise<void> {
    this.abort = new AbortController();
    const headers: Record<string, string> = { ...this.reader.headers, accept: 'text/event-stream' };
    if (this.lastId > 0) headers['Last-Event-ID'] = String(this.lastId);
    const response = await fetch(this.reader.url, { headers, signal: this.abort.signal });
    if (!response.ok || response.body === null) {
      throw new Error(`GET ${this.reader.url} answered ${response.status}: ${await response.text()}`);
    }
    const body = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    // The first chunk of the body is the reserved open-of-stream comment, sent once the subscription is in place.
    const opening = await body.read();
    if (!opening.done) buffer += decoder.decode(opening.value, { stream: true });
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

  waitFor(predicate: (frame: Frame) => boolean, options: WaitOptions = {}): Promise<Frame> {
    const timeout = options.timeout ?? DEFAULT_WAIT_MS;
    return new Promise((resolve, reject) => {
      // The ledger can grow without a frame arriving, so a wait looks again on a short interval as well as on each frame.
      const poll = setInterval(() => check(), 50);
      const timer = setTimeout(() => {
        finish();
        reject(new Error(`no frame matched within ${timeout}ms; saw ${this.frames.map((frame) => `${frame.id}:${frame.event}`).join(', ') || 'none'}`));
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
        const match = this.frames.find(predicate);
        if (match !== undefined) {
          finish();
          resolve(match);
        }
      };
      this.waiters.add(check);
      check();
    });
  }

  close(): void {
    this.closed = true;
    this.abort.abort();
  }
}

/**
 * The hub's event stream, read with Node's own streaming `fetch` because Playwright's request context reads a response
 * whole. At the levels that share a tenant, a frame is passed on only if it names an id the ledger holds.
 */
export function eventStream(options: { hubUrl: string; tenantId: string; ledger: Ledger; narrow: boolean; opened: Subscription[] }): Events {
  const stream = (narrow: boolean): EventStream => ({
    async open() {
      const admits = (frame: Frame): boolean =>
        !narrow || Object.values(frame.data ?? {}).some((value) => options.ledger.holds(value));
      const subscription = new StreamReader({
        url: `${options.hubUrl}/api/events/stream`,
        headers: { 'X-Blizzard-Tenant': options.tenantId },
        admits,
      });
      await subscription.start();
      options.opened.push(subscription);
      return subscription;
    },
  });
  return { ...stream(options.narrow), unfiltered: stream(false) };
}

/** A reader over any stream URL; the harness's own proof points it at a stub. */
export async function openStream(reader: Reader): Promise<Subscription> {
  const subscription = new StreamReader(reader);
  await subscription.start();
  return subscription;
}
