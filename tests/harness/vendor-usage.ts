import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as http from 'node:http';
import type { AddressInfo } from 'node:net';
import * as path from 'node:path';

/** One window a credential's usage reports: how much is used, and when it resets. */
export interface UsageWindow {
  utilization: number;
  /** Epoch seconds. */
  resetsAt: number;
}

/** What one credential's usage answers: the short and the long window. */
export interface UsageLevels {
  short: UsageWindow;
  long: UsageWindow;
}

/** A request the counterpart answered, as a check reads it back. */
export interface VendorRequest {
  vendor: 'anthropic' | 'openai';
  token: string;
  accountId: string | undefined;
  status: number;
}

/** A credential file the counterpart minted: where it is, and the token the counterpart answers it under. */
export interface MintedCredential {
  path: string;
  token: string;
}

/**
 * A loopback stand-in for the vendors' usage endpoints, in the test's own worker. It serves Anthropic's
 * `GET /api/oauth/usage` and OpenAI's `GET /codex/usage`, keyed by bearer token, and answers a token it never minted
 * `401`, as the vendor does an expired one.
 */
export interface VendorUsage {
  /** The base a subscription's `usage_api_base` names. */
  readonly url: string;
  /** Writes a synthetic Anthropic credential file under `dir`, with a token no real login holds and a far-future expiry. */
  mintAnthropic(dir: string, levels?: UsageLevels): MintedCredential;
  /** Writes a synthetic OpenAI credential file under `dir`; the token is opaque, so it carries no expiry to lapse. */
  mintOpenAI(dir: string, levels?: UsageLevels): MintedCredential;
  /** Sets what a minted credential's usage reports from the next request on. */
  set(token: string, levels: UsageLevels): void;
  /** Every request answered so far, oldest first. */
  readonly requests: readonly VendorRequest[];
  close(): Promise<void>;
}

const FAR_FUTURE_MS = Date.UTC(2099, 0, 1);
const SHORT_WINDOW_SECONDS = 18_000;
const LONG_WINDOW_SECONDS = 604_800;

/** The levels a credential starts with: lightly used, resetting in the future. */
export function defaultLevels(): UsageLevels {
  const now = Math.floor(Date.now() / 1000);
  return {
    short: { utilization: 12, resetsAt: now + SHORT_WINDOW_SECONDS },
    long: { utilization: 34, resetsAt: now + LONG_WINDOW_SECONDS },
  };
}

function anthropicBody(levels: UsageLevels): unknown {
  const iso = (seconds: number): string => new Date(seconds * 1000).toISOString();
  return {
    five_hour: { utilization: levels.short.utilization, resets_at: iso(levels.short.resetsAt) },
    seven_day: { utilization: levels.long.utilization, resets_at: iso(levels.long.resetsAt) },
  };
}

function openaiBody(levels: UsageLevels): unknown {
  return {
    rate_limit: {
      primary_window: { used_percent: levels.short.utilization, limit_window_seconds: SHORT_WINDOW_SECONDS, reset_at: levels.short.resetsAt },
      secondary_window: { used_percent: levels.long.utilization, limit_window_seconds: LONG_WINDOW_SECONDS, reset_at: levels.long.resetsAt },
    },
  };
}

export async function startVendorUsage(): Promise<VendorUsage> {
  const credentials = new Map<string, { vendor: 'anthropic' | 'openai'; levels: UsageLevels; accountId?: string }>();
  const requests: VendorRequest[] = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url ?? '/', 'http://localhost');
    const vendor = url.pathname === '/api/oauth/usage' ? 'anthropic' : url.pathname === '/codex/usage' ? 'openai' : undefined;
    const token = /^Bearer (.+)$/.exec(request.headers.authorization ?? '')?.[1] ?? '';
    const accountId = request.headers['chatgpt-account-id'];
    const known = credentials.get(token);
    const respond = (status: number, body: unknown): void => {
      if (vendor !== undefined) requests.push({ vendor, token, accountId: typeof accountId === 'string' ? accountId : undefined, status });
      response.writeHead(status, { 'content-type': 'application/json' });
      response.end(JSON.stringify(body));
    };
    if (request.method !== 'GET' || vendor === undefined) return respond(404, { error: 'not found' });
    const accepted = known !== undefined && known.vendor === vendor && (vendor === 'anthropic' || known.accountId === accountId);
    if (!accepted) return respond(401, { error: 'unauthorized' });
    respond(200, vendor === 'anthropic' ? anthropicBody(known.levels) : openaiBody(known.levels));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;

  const write = (file: string, body: unknown): void => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(body), { mode: 0o600 });
  };
  return {
    url: `http://127.0.0.1:${port}`,
    mintAnthropic(dir, levels = defaultLevels()) {
      const token = `synthetic-anthropic-${crypto.randomBytes(8).toString('hex')}`;
      const file = path.join(dir, `anthropic-${token.slice(-8)}.json`);
      write(file, { claudeAiOauth: { accessToken: token, refreshToken: 'synthetic-refresh', expiresAt: FAR_FUTURE_MS } });
      credentials.set(token, { vendor: 'anthropic', levels });
      return { path: file, token };
    },
    mintOpenAI(dir, levels = defaultLevels()) {
      const token = `synthetic-openai-${crypto.randomBytes(8).toString('hex')}`;
      const accountId = `acct-${token.slice(-8)}`;
      const file = path.join(dir, `openai-${token.slice(-8)}.json`);
      write(file, { tokens: { access_token: token, refresh_token: 'synthetic-refresh', account_id: accountId } });
      credentials.set(token, { vendor: 'openai', levels, accountId });
      return { path: file, token };
    },
    set(token, levels) {
      const known = credentials.get(token);
      if (known === undefined) throw new Error('that token was not minted by this counterpart, so it has no usage to set');
      known.levels = levels;
    },
    requests,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}
