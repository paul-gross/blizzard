import type { LogContext } from './engine';
import { socketRequest } from './runner';

// A hand-written client for the controlled clock routes, which are in no OpenAPI document. It reads and sets with
// `{ "to": … }` only, so a daemon that drifted is corrected rather than carried. It imports nothing from the hub world.

/** One daemon's clock: read where it stands, and set it forward to an instant. */
export interface ClockClient {
  read(): Promise<Date>;
  /** Sets the clock to `to` and resolves with the instant the daemon answers. */
  set(to: Date): Promise<Date>;
}

/** A daemon whose clock route answered `404`: it did not take the controlled mode. */
export class ClockRouteMissing extends Error {}

/**
 * An instant as the harness records it: to the millisecond, rounded up. A daemon's clock holds microseconds, a `Date`
 * milliseconds, and setting a daemon to a truncated reading of its own would be a move backwards.
 */
export function parseInstant(text: string): Date {
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) throw new Error(`a clock answered an instant the harness cannot read: ${JSON.stringify(text)}`);
  const beyond = /\.\d{3}(\d+)/.exec(text)?.[1];
  return beyond !== undefined && /[1-9]/.test(beyond) ? new Date(parsed.getTime() + 1) : parsed;
}

interface Answer {
  status: number;
  body: unknown;
  text: string;
}

function instantOf(answer: Answer, what: string, call: string): Date {
  if (answer.status === 404) throw new ClockRouteMissing(`${call} answered 404`);
  if (answer.status !== 200) throw new Error(`${what}: ${call} answered ${answer.status}: ${answer.text}`);
  const now = (answer.body as { now?: unknown } | undefined)?.now;
  if (typeof now !== 'string') throw new Error(`${what}: ${call} answered no \`now\`: ${answer.text}`);
  return parseInstant(now);
}

function parsed(status: number, text: string): Answer {
  let body: unknown = text;
  try {
    body = text === '' ? undefined : JSON.parse(text);
  } catch {
    // Not JSON: the text stands.
  }
  return { status, body, text };
}

function over(what: string, route: string, request: (method: string, data?: unknown) => Promise<Answer>): ClockClient {
  return {
    read: async () => instantOf(await request('GET'), what, `GET ${route}`),
    set: async (to) => instantOf(await request('POST', { to: to.toISOString() }), what, `POST ${route}`),
  };
}

/** The hub's `/api/admin/clock`, as the superuser on a hub with authentication on (`session` supplies the cookie). */
export function hubClock(url: string, session?: () => Promise<string | undefined>): ClockClient {
  return over('the hub', '/api/admin/clock', async (method, data) => {
    const cookie = await session?.();
    const response = await fetch(`${url}/api/admin/clock`, {
      method,
      headers: { ...(cookie === undefined ? {} : { cookie: `bz_session=${cookie}` }), ...(data === undefined ? {} : { 'content-type': 'application/json' }) },
      body: data === undefined ? undefined : JSON.stringify(data),
      signal: AbortSignal.timeout(30_000),
    });
    return parsed(response.status, await response.text());
  });
}

/** A runner host's `/api/host/clock`, over its `host.sock`. */
export function hostClock(socket: string, logContext: LogContext): ClockClient {
  return over('the runner host', '/api/host/clock', async (method, data) => {
    const answer = await socketRequest({ socket, logContext }, method, '/api/host/clock', data === undefined ? {} : { 'content-type': 'application/json' }, data === undefined ? undefined : JSON.stringify(data));
    return parsed(answer.status, answer.text);
  });
}

/** A mock daemon's `/_clock`: the mock hub, a mock runner, the mock forge. */
export function mockClock(url: string): ClockClient {
  return over('the mock', '/_clock', async (method, data) => {
    const response = await fetch(`${url}/_clock`, {
      method,
      headers: data === undefined ? {} : { 'content-type': 'application/json' },
      body: data === undefined ? undefined : JSON.stringify(data),
      signal: AbortSignal.timeout(30_000),
    });
    return parsed(response.status, await response.text());
  });
}
