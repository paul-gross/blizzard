import * as fs from 'node:fs';
import * as http from 'node:http';
import * as path from 'node:path';
import { expect as plainExpect, test as plain } from '@playwright/test';
import { HubInternal } from '../client/internal';
import { BLIZZARD_ROOT } from '../env';
import { type Frame, openStream, parseFrames } from '../events';
import { expect, test } from '../fixtures';

const SSE = path.join(BLIZZARD_ROOT, 'contracts', 'sse');

/** Every golden payload, in file order, as `[kind, case, payload]`. */
function goldens(): [string, string, unknown][] {
  const kinds = (JSON.parse(fs.readFileSync(path.join(SSE, 'manifest.json'), 'utf8')) as { kinds: string[] }).kinds;
  return kinds.flatMap((kind) => {
    const cases = JSON.parse(fs.readFileSync(path.join(SSE, `${kind}.json`), 'utf8')) as Record<string, unknown>;
    return Object.entries(cases).map(([name, payload]): [string, string, unknown] => [kind, name, payload]);
  });
}

const framed = (id: number, kind: string, payload: unknown): string => `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify(payload)}\n\n`;

plain('every golden frame parses to its payload', () => {
  const all = goldens();
  const text = all.map(([kind, , payload], index) => framed(index + 1, kind, payload)).join('');
  const { frames, rest } = parseFrames(`: blizzard hub event stream\n\n${text}: keepalive\n\n`);
  expect(rest).toBe('');
  expect(frames.map((frame) => [frame.event, frame.data])).toEqual(all.map(([kind, , payload]) => [kind, payload]));
});

plain('a dropped connection resumes with Last-Event-ID and serves no frame twice', async () => {
  const requests: (string | undefined)[] = [];
  const served = [1, 2, 3, 4, 5].map((id) => framed(id, 'queue-changed', { id }));
  const server = http.createServer((request, response) => {
    const header = request.headers['last-event-id'];
    requests.push(Array.isArray(header) ? header[0] : header);
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    response.write(': blizzard hub event stream\n\n');
    if (requests.length === 1) {
      // The first connection delivers three frames and drops.
      response.write(served.slice(0, 3).join(''));
      setTimeout(() => response.destroy(), 50);
      return;
    }
    // The resume replays one frame the reader already passed on, then the rest.
    response.write(served.slice(2).join(''));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/events/stream`;
  const subscription = await openStream({ url, headers: {}, admits: () => true });
  try {
    await subscription.waitFor((frame) => frame.id === 5, { timeout: 5_000 });
    expect(requests).toEqual([undefined, '3']);
    expect(subscription.frames.map((frame) => frame.id)).toEqual([1, 2, 3, 4, 5]);
  } finally {
    subscription.close();
    server.closeAllConnections();
    server.close();
  }
});

plain('waitFor rejects at its timeout rather than ending quietly', async () => {
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    response.write(': blizzard hub event stream\n\n');
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/events/stream`;
  const subscription = await openStream({ url, headers: {}, admits: () => true });
  try {
    await plainExpect(subscription.waitFor(() => true, { timeout: 300 })).rejects.toThrow(/no frame matched within 300ms/);
  } finally {
    subscription.close();
    server.closeAllConnections();
    server.close();
  }
});

/** A stub stream: `serve` writes each connection's frames, and the requests it was asked for are recorded. */
async function stub(serve: (response: http.ServerResponse, number: number) => void): Promise<{
  url: string;
  requests: { url: string; lastEventId: string | undefined }[];
  stop: () => void;
}> {
  const requests: { url: string; lastEventId: string | undefined }[] = [];
  const server = http.createServer((request, response) => {
    const header = request.headers['last-event-id'];
    requests.push({ url: request.url ?? '', lastEventId: Array.isArray(header) ? header[0] : header });
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    response.write(': blizzard hub event stream\n\n');
    serve(response, requests.length);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/events/stream`;
  return {
    url,
    requests,
    stop: () => {
      server.closeAllConnections();
      server.close();
    },
  };
}

plain('a stream opened without reconnecting resolves its end wait when the hub ends it, and does not resume', async () => {
  const hub = await stub((response) => {
    response.write(framed(1, 'queue-changed', {}));
    setTimeout(() => response.end(), 100);
  });
  const subscription = await openStream({ url: hub.url, headers: {}, admits: () => true, reconnect: false });
  try {
    expect(subscription.ended).toBe(false);
    await subscription.waitForEnd({ timeout: 5_000 });
    expect(subscription.ended).toBe(true);
    expect(subscription.frames.map((frame) => frame.id)).toEqual([1]);
    expect(hub.requests).toHaveLength(1);
  } finally {
    subscription.close();
    hub.stop();
  }
});

plain('the end wait on an open stream fails at its bound, and a reconnecting stream refuses the wait', async () => {
  const hub = await stub(() => undefined);
  const open = await openStream({ url: hub.url, headers: {}, admits: () => true, reconnect: false });
  const resuming = await openStream({ url: hub.url, headers: {}, admits: () => true });
  try {
    await plainExpect(open.waitForEnd({ timeout: 300 })).rejects.toThrow(/the stream did not end within 300ms/);
    expect(open.ended).toBe(false);
    await plainExpect(resuming.waitForEnd({ timeout: 300 })).rejects.toThrow(/needs a subscription opened with `reconnect: false`/);
  } finally {
    open.close();
    resuming.close();
    hub.stop();
  }
});

plain('a stream joined from now connects with from=latest, and its resume carries only Last-Event-ID', async () => {
  const hub = await stub((response, number) => {
    if (number === 1) {
      response.write(framed(7, 'queue-changed', {}));
      setTimeout(() => response.destroy(), 50);
      return;
    }
    response.write(framed(8, 'queue-changed', {}));
  });
  const subscription = await openStream({ url: hub.url, headers: {}, admits: () => true, fromNow: true });
  try {
    await subscription.waitFor((frame) => frame.id === 8, { timeout: 5_000 });
    expect(hub.requests).toEqual([
      { url: '/api/events/stream?from=latest', lastEventId: undefined },
      { url: '/api/events/stream', lastEventId: '7' },
    ]);
  } finally {
    subscription.close();
    hub.stop();
  }
});

plain('a subscription keeps the first connection’s opening comment and content-type through a reconnect', async () => {
  const hub = await stub((response, number) => {
    if (number === 1) {
      response.write(framed(1, 'queue-changed', {}));
      setTimeout(() => response.destroy(), 50);
      return;
    }
    response.write(framed(2, 'queue-changed', {}));
  });
  const subscription = await openStream({ url: hub.url, headers: {}, admits: () => true });
  try {
    await subscription.waitFor((frame) => frame.id === 2, { timeout: 5_000 });
    expect(hub.requests).toHaveLength(2);
    expect(subscription.openingComment).toBe(': blizzard hub event stream');
    expect(subscription.contentType).toBe('text/event-stream');
  } finally {
    subscription.close();
    hub.stop();
  }
});

plain('a stream with no join policy connects with no cursor', async () => {
  const hub = await stub(() => undefined);
  const subscription = await openStream({ url: hub.url, headers: {}, admits: () => true });
  try {
    expect(hub.requests).toEqual([{ url: '/api/events/stream', lastEventId: undefined }]);
  } finally {
    subscription.close();
    hub.stop();
  }
});

plain('a stream joined from now that drops before any frame fails its pending and later waits, naming the gap', async () => {
  const hub = await stub((response, number) => {
    if (number === 1) setTimeout(() => response.destroy(), 50);
  });
  const subscription = await openStream({ url: hub.url, headers: {}, admits: () => true, fromNow: true });
  try {
    const pending = subscription.waitFor(() => false, { timeout: 5_000 });
    await plainExpect(pending).rejects.toThrow(/no cursor to resume from.*unrecoverable/);
    await plainExpect(subscription.waitFor(() => true, { timeout: 5_000 })).rejects.toThrow(/no cursor to resume from/);
    await plainExpect(subscription.waitForAll([() => true], { timeout: 5_000 })).rejects.toThrow(/no cursor to resume from/);
    expect(hub.requests).toHaveLength(1);
  } finally {
    subscription.close();
    hub.stop();
  }
});

plain('waitForAll resolves when its last predicate’s frame arrives, one frame per predicate in predicate order', async () => {
  const hub = await stub((response) => {
    response.write(framed(1, 'queue-changed', {}));
    setTimeout(() => response.write(framed(2, 'chunk-changed', { chunk_id: 'ch_a' })), 100);
    setTimeout(() => response.write(framed(3, 'chunk-changed', { chunk_id: 'ch_b' })), 300);
  });
  const subscription = await openStream({ url: hub.url, headers: {}, admits: () => true });
  try {
    const started = Date.now();
    const found = await subscription.waitForAll(
      [(frame) => frame.data.chunk_id === 'ch_b', (frame) => frame.event === 'queue-changed', (frame) => frame.event === 'queue-changed'],
      { timeout: 5_000 },
    );
    expect(Date.now() - started).toBeGreaterThanOrEqual(250);
    expect(found.map((frame) => frame.id)).toEqual([3, 1, 1]);
  } finally {
    subscription.close();
    hub.stop();
  }
});

plain('waitForAll fails at its timeout naming the unmet predicate and the frames seen', async () => {
  const hub = await stub((response) => response.write(framed(1, 'queue-changed', {})));
  const subscription = await openStream({ url: hub.url, headers: {}, admits: () => true });
  try {
    await plainExpect(
      subscription.waitForAll([(frame) => frame.event === 'queue-changed', (frame) => frame.event === 'never-published'], { timeout: 300 }),
    ).rejects.toThrow(/1 of 2 predicates unmatched \(#1 .*never-published.*\) within 300ms; saw 1:queue-changed/);
  } finally {
    subscription.close();
    hub.stop();
  }
});

test.describe('against the real shared hub at common', () => {
  test.use({ isolation: 'common' });

  test('waitFor resolves on the test’s own chunk, and the stream drops a frame naming an id the ledger does not hold', async ({
    events,
    issue,
    hub: shared,
  }) => {
    const hub = shared.coordinates();
    const subscription = await events.open();
    const everything = await events.unfiltered.open();

    // The neighbour is made by this check itself, through the internal client, so the proof depends on no other test.
    const filed = await new HubInternal(hub.forgeUrl).call<{ number: number }>('POST', '/repos/blizzard/common/issues', {
      body: { title: 'a neighbour’s' },
    });
    const neighbour = await new HubInternal(hub.url).call<{ chunk_id: string }>('POST', '/api/chunks', {
      tenant: hub.commonTenantId,
      body: { tokens: [`common:${filed.number}`], project: 'default' },
    });
    const own = await issue('the test’s own');

    const named = (id: string) => (frame: Frame) => frame.event === 'chunk-changed' && frame.data.chunk_id === id;
    const frame = await subscription.waitFor(named(own.chunk.chunk_id));
    expect(frame.data.chunk_id).toBe(own.chunk.chunk_id);

    // The unfiltered stream saw the neighbour's frame, which is the positive control for the drop.
    await everything.waitFor(named(neighbour.chunk_id));
    expect(subscription.frames.filter(named(neighbour.chunk_id))).toEqual([]);
  });
});

const named = (id: string) => (frame: Frame) => frame.event === 'chunk-changed' && frame.data.chunk_id === id;
const names = (id: string) => (frame: Frame) => Object.values(frame.data ?? {}).includes(id);

test.describe('the join from now at common', () => {
  test.use({ isolation: 'common' });

  test('a subscription opened after the test’s first chunk sees none of its frames, and a later act’s', async ({ events, issue }) => {
    const first = await issue('filed before the subscription');
    const subscription = await events.open();
    const later = await issue('filed after the subscription');
    await subscription.waitFor(named(later.chunk.chunk_id));
    expect(subscription.frames.filter(names(first.chunk.chunk_id))).toEqual([]);
  });
});

test.describe('the barrier at common', () => {
  test.use({ isolation: 'common' });

  test('brings back a frame of the test’s own act and hides every frame naming the barrier chunk', async ({ events, issue, ledger }) => {
    const subscription = await events.open({ barrier: true });
    expect(ledger.holds(subscription.barrierChunkId)).toBe(true);
    const own = await issue('the act the barrier waits behind');
    const answer = await subscription.barrier();
    expect(answer.filter(named(own.chunk.chunk_id))).toHaveLength(1);
    expect(answer.filter(names(subscription.barrierChunkId))).toEqual([]);
    expect(subscription.frames.filter(names(subscription.barrierChunkId))).toEqual([]);
    expect(subscription.frames.filter(named(own.chunk.chunk_id))).toHaveLength(1);
    // A second barrier answers for the frames since the first.
    expect(await subscription.barrier()).toEqual([]);
  });
});

test.describe('the barrier act at fresh-tenant', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('puts exactly one chunk-changed (edited) on the stream per call, and no queue-changed', async ({ events, testHub, tenant }) => {
    const subscription = await events.unfiltered.open({ barrier: true });
    const raw = await openStream({
      url: `${testHub.coordinates.url}/api/events/stream`,
      headers: { 'X-Blizzard-Tenant': tenant.id },
      admits: () => true,
      fromNow: true,
    });
    try {
      expect(await subscription.barrier()).toEqual([]);
      expect(await subscription.barrier()).toEqual([]);
      await raw.waitForAll([named(subscription.barrierChunkId), (frame) => frame.id > (raw.frames[0]?.id ?? 0)]);
      expect(raw.frames.map((frame) => [frame.event, frame.data.chunk_id, frame.data.cause])).toEqual([
        ['chunk-changed', subscription.barrierChunkId, 'edited'],
        ['chunk-changed', subscription.barrierChunkId, 'edited'],
      ]);
      expect(subscription.frames).toEqual([]);
    } finally {
      raw.close();
    }
  });
});
