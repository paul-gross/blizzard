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
