import * as fs from 'node:fs';
import * as http from 'node:http';
import * as path from 'node:path';
import * as protobuf from 'protobufjs';
import { test, expect } from '@playwright/test';
import { assertChunkTrace, assertConforms, assertIdentity, assertInvocations, assertNoLeaks, assertPlatformNesting, assertRunnerNesting, assertSkeleton, chunkSpanId, chunkTraceId, isFleet, lifetimeSpanId, lifetimeTraceId, roleOf, spanId, stepKeyText, tracesOf } from '../../fixtures';
import { startOtlpSink, type ReceivedSpan } from '../../otlp-sink';

const dictionary = JSON.parse(fs.readFileSync(path.join(__dirname, '../../../../contracts/traces/dictionary.json'), 'utf8')) as {
  schema_version: string;
  attributes: { name: string; type: string; on: string[]; optional: boolean; optional_on?: string[] }[];
  ids: { vectors: { chunk_id: string; epoch: number; decision_id?: string; role: string; discriminator: string; key: string; span_id: string; trace_id: string; parent_span_id?: string }[];
    chunk_vectors: { chunk_id: string; span_id: string; trace_id: string }[];
    lifetime_vectors: { chunk_id: string; role: string; discriminator: string; span_id: string; trace_id: string }[] };
};
const chunk = 'ch_1';
const trace = chunkTraceId(chunk);
const rootId = spanId({ chunkId: chunk, epoch: 1 }, 'step');
const lifeId = lifetimeSpanId(chunk);
const attrs = (role: string): Record<string, unknown> => Object.fromEntries(dictionary.attributes.filter((item) => !item.optional && item.on.includes(role) && !item.optional_on?.includes(role)).map((item) => [item.name, item.type === 'string' ? 'x' : item.type === 'bool' ? false : item.type === 'string[]' ? [] : item.type === 'double' ? 1.5 : 1]));
function span(name: string, role: string, overrides: Partial<ReceivedSpan> = {}): ReceivedSpan {
  return {
    name, traceId: trace, spanId: '1111111111111111', parentSpanId: rootId, traceState: '', kind: 1,
    startTimeUnixNano: '10', endTimeUnixNano: '20', status: { code: 0, message: '' }, flags: 1,
    droppedAttributesCount: 0, droppedEventsCount: 0, droppedLinksCount: 0, events: [], links: [],
    attributes: { ...attrs(role), 'blizzard.chunk.id': chunk },
    scope: { name: role.startsWith('runner/') ? 'blizzard.runner.runner_spans' : 'blizzard.hub.fleet_spans', version: '3' },
    resource: { 'service.name': role.startsWith('chunk/') ? 'blizzard-chunk' : 'blizzard-hub', 'service.version': '1', 'blizzard.trace.schema_version': dictionary.schema_version },
    ...overrides,
  };
}
function link(traceId: string, spanId_: string, reason: string): ReceivedSpan['links'][number] {
  return { traceId, spanId: spanId_, traceState: '', attributes: { 'blizzard.link.reason': reason }, droppedAttributesCount: 0, flags: 0 };
}
function world(): ReceivedSpan[] {
  const root = span('step build', 'step', { spanId: rootId, parentSpanId: chunkSpanId(chunk), attributes: { ...attrs('step'), 'blizzard.chunk.id': chunk, 'blizzard.node.name': 'build', 'blizzard.step.epoch': 1, 'blizzard.step.outcome': 'done' } });
  const work = span('chunk work', 'chunk', { spanId: chunkSpanId(chunk), parentSpanId: null, attributes: { ...attrs('chunk'), 'blizzard.chunk.id': chunk, 'blizzard.chunk.outcome': 'done', 'blizzard.chunk.steps': 1 }, links: [link(lifetimeTraceId(chunk), lifeId, 'lifetime')] });
  const life = span('chunk', 'chunk/lifetime', { traceId: lifetimeTraceId(chunk), spanId: lifeId, parentSpanId: null, startTimeUnixNano: '1', attributes: { ...attrs('chunk/lifetime'), 'blizzard.chunk.id': chunk, 'blizzard.chunk.outcome': 'done', 'blizzard.chunk.steps': 1 } });
  const stepAttrs: Record<string, unknown> = { ...attrs('chunk/step'), 'blizzard.chunk.id': chunk, 'blizzard.step.kind': 'step', 'blizzard.step.epoch': 1, 'blizzard.step.outcome': 'done' };
  for (const key of ['blizzard.step.wait.queue_ms', 'blizzard.step.wait.claim_ms', 'blizzard.step.wait.ask_ms', 'blizzard.step.wait.pause_ms', 'blizzard.step.wait.pickup_ms']) stepAttrs[key] = root.attributes[key];
  const told = span('build', 'chunk/step', { traceId: lifetimeTraceId(chunk), spanId: lifetimeSpanId(chunk, 'chunk/step', 'ch_1/1'), parentSpanId: lifeId, attributes: stepAttrs, links: [link(trace, rootId, 'work')] });
  const wait = span('backlog wait', 'chunk/backlog-wait', { traceId: lifetimeTraceId(chunk), spanId: '2222222222222222', parentSpanId: lifeId, startTimeUnixNano: '1', endTimeUnixNano: '10' });
  return [work, root, life, told, wait];
}

test('every published identity vector derives', () => {
  expect(dictionary.ids.vectors.length).toBeGreaterThan(0);
  for (const vector of dictionary.ids.vectors) {
    const key = { chunkId: vector.chunk_id, epoch: vector.epoch, decisionId: vector.decision_id };
    expect(stepKeyText(key)).toBe(vector.key);
    expect(spanId(key, vector.role, vector.discriminator)).toBe(vector.span_id);
    expect(chunkTraceId(vector.chunk_id)).toBe(vector.trace_id);
    if (vector.parent_span_id) expect(spanId(key, 'step')).toBe(vector.parent_span_id);
  }
  for (const vector of dictionary.ids.chunk_vectors) {
    expect(chunkSpanId(vector.chunk_id)).toBe(vector.span_id);
    expect(chunkTraceId(vector.chunk_id)).toBe(vector.trace_id);
  }
  for (const vector of dictionary.ids.lifetime_vectors) {
    expect(lifetimeSpanId(vector.chunk_id, vector.role, vector.discriminator)).toBe(vector.span_id);
    expect(lifetimeTraceId(vector.chunk_id)).toBe(vector.trace_id);
  }
});

test('dictionary conformance accepts the synthetic fleet and rejects wrong names, types, scope, events and links', () => {
  const spans = world();
  expect(spans.every(isFleet)).toBe(true);
  assertConforms(spans);
  const root = spans[1]!;
  expect(roleOf(root)).toBe('step');
  expect(() => assertConforms([{ ...root, name: 'unknown' }])).toThrow('not in the dictionary');
  expect(() => assertConforms([{ ...root, attributes: { ...root.attributes, 'blizzard.chunk.id': 4 } }])).toThrow('wrong type');
  expect(() => assertConforms([{ ...root, scope: { name: 'bad', version: '' } }])).toThrow('scope');
  expect(() => assertConforms([{ ...root, events: [{ name: 'bad', timeUnixNano: '10', attributes: {}, droppedAttributesCount: 0 }] }])).toThrow('unknown event');
  expect(() => assertConforms([{ ...root, links: [link(trace, rootId, 'bad')] }])).toThrow('unknown link');
  const missing = { ...root.attributes }; delete missing['blizzard.chunk.id'];
  expect(() => assertConforms([{ ...root, attributes: missing }])).toThrow('lacks required');
});

test('identity, skeleton and the two chunk traces reject changed parents, links and times', () => {
  const spans = world();
  const traces = tracesOf(spans);
  assertIdentity(traces);
  assertSkeleton(traces, [{ name: 'step build', outcome: 'done' }]);
  assertChunkTrace(spans, chunk, { outcome: 'done', steps: 1 });
  expect(() => assertIdentity([{ ...traces[0]!, root: { ...traces[0]!.root, spanId: 'bad' } }])).toThrow('span id');
  expect(() => assertSkeleton(traces, [{ name: 'step build', outcome: 'failed' }])).toThrow();
  expect(() => assertChunkTrace(spans.map((item) => item.name === 'build' ? { ...item, links: [link(trace, 'bad', 'work')] } : item), chunk, { outcome: 'done', steps: 1 })).toThrow('no work root');
  expect(() => assertChunkTrace(spans.map((item) => item.name === 'backlog wait' ? { ...item, endTimeUnixNano: '11' } : item), chunk, { outcome: 'done', steps: 1 })).toThrow('overlap');
});

test('gate identity requires the decision and children must nest on the right root', () => {
  const gateId = spanId({ chunkId: chunk, epoch: 1, decisionId: 'd1' }, 'gate');
  const gate = span('gate review', 'gate', { spanId: gateId, parentSpanId: chunkSpanId(chunk), attributes: { ...attrs('gate'), 'blizzard.chunk.id': chunk, 'blizzard.step.epoch': 1 } });
  const child = span('decision pickup', 'pickup', { parentSpanId: gateId });
  const traces = tracesOf([gate, child]);
  assertIdentity(traces, { decisionIds: ['d1'] });
  expect(() => assertIdentity(traces, { decisionIds: ['other'] })).toThrow('span id');
  expect(() => tracesOf([gate, { ...child, parentSpanId: 'bad' }])).toThrow('no exported step root');
  expect(() => assertIdentity([{ root: gate, children: [{ ...child, traceId: 'bad' }] }], { decisionIds: ['d1'] })).toThrow();
});

test('runner nesting and invocation content reject broken parentage and usage', () => {
  const root = world()[1]!;
  const worker = span('worker build', 'runner/worker', { spanId: '3333333333333333', parentSpanId: rootId });
  const invocation = span('invoke_agent', 'runner/invocation', { parentSpanId: worker.spanId, attributes: { ...attrs('runner/invocation'), 'blizzard.chunk.id': chunk, 'blizzard.node.name': 'build', 'blizzard.harness.id': 'mock', 'gen_ai.operation.name': 'invoke_agent', 'gen_ai.response.model': 'model', 'gen_ai.usage.input_tokens': 1, 'gen_ai.usage.output_tokens': 2 } });
  const runners = assertRunnerNesting([root, worker, invocation], 1);
  assertInvocations(runners, { build: { harnessId: 'mock', responseModel: 'model' } });
  expect(() => assertRunnerNesting([root, worker, { ...invocation, parentSpanId: 'bad' }], 1)).toThrow('parent');
  expect(() => assertRunnerNesting([root, worker], 1)).toThrow('no invocation');
  expect(() => assertInvocations([{ ...invocation, attributes: { ...invocation.attributes, 'gen_ai.usage.input_tokens': 0 } }], { build: { harnessId: 'mock', responseModel: 'model' } })).toThrow('input_tokens');
});

test('platform chain and leak scan reject missing query and planted values', () => {
  const root = world()[1]!;
  const platform = (id: string, parentId: string, scope: string, service: string, attributes: Record<string, unknown> = {}): ReceivedSpan => span(id, 'step', { spanId: id, parentSpanId: parentId, scope: { name: scope, version: '' }, resource: { 'service.name': service }, attributes });
  const command = platform('command', rootId, 'blizzard.cli', 'blizzard-cli', { 'blizzard.chunk.id': chunk, 'blizzard.cli.command': 'runner work-items' });
  const server = platform('server', 'command', 'fastapi', 'blizzard-runner');
  const client = platform('client', 'server', 'opentelemetry.instrumentation.httpx', 'blizzard-runner');
  const hub = platform('hub', 'client', 'fastapi', 'blizzard-hub');
  const query = platform('query', 'hub', 'opentelemetry.instrumentation.sqlalchemy', 'blizzard-hub');
  assertPlatformNesting([root], [command, server, client, hub, query]);
  expect(() => assertPlatformNesting([root], [command, server, client, hub])).toThrow('no query');
  assertNoLeaks([root], { token: ['secret'] });
  expect(() => assertNoLeaks([root], { token: [] })).toThrow('nothing was planted');
  expect(() => assertNoLeaks([{ ...root, scope: { ...root.scope, attributes: { key: 'secret' } } }], { token: ['secret'] })).toThrow('planted token');
});

test('the sink decodes posted spans with exact nanoseconds, ordered events, links and nested values', async () => {
  const schema = protobuf.Root.fromJSON({ nested: {
    Value: { fields: { stringValue: { type: 'string', id: 1 }, intValue: { type: 'int64', id: 3 }, doubleValue: { type: 'double', id: 4 }, arrayValue: { type: 'ArrayValue', id: 5 }, kvlistValue: { type: 'KeyValueList', id: 6 }, bytesValue: { type: 'bytes', id: 7 } } },
    ArrayValue: { fields: { values: { rule: 'repeated', type: 'Value', id: 1 } } },
    KeyValueList: { fields: { values: { rule: 'repeated', type: 'KV', id: 1 } } },
    KV: { fields: { key: { type: 'string', id: 1 }, value: { type: 'Value', id: 2 } } },
    Event: { fields: { timeUnixNano: { type: 'fixed64', id: 1 }, name: { type: 'string', id: 2 }, attributes: { rule: 'repeated', type: 'KV', id: 3 } } },
    Link: { fields: { traceId: { type: 'bytes', id: 1 }, spanId: { type: 'bytes', id: 2 }, attributes: { rule: 'repeated', type: 'KV', id: 4 } } },
    Status: { fields: { message: { type: 'string', id: 2 }, code: { type: 'int32', id: 3 } } },
    Span: { fields: { traceId: { type: 'bytes', id: 1 }, spanId: { type: 'bytes', id: 2 }, parentSpanId: { type: 'bytes', id: 4 }, name: { type: 'string', id: 5 }, startTimeUnixNano: { type: 'fixed64', id: 7 }, endTimeUnixNano: { type: 'fixed64', id: 8 }, attributes: { rule: 'repeated', type: 'KV', id: 9 }, events: { rule: 'repeated', type: 'Event', id: 11 }, links: { rule: 'repeated', type: 'Link', id: 13 }, status: { type: 'Status', id: 15 } } },
    ScopeSpans: { fields: { spans: { rule: 'repeated', type: 'Span', id: 2 } } },
    ResourceSpans: { fields: { scopeSpans: { rule: 'repeated', type: 'ScopeSpans', id: 2 } } },
    Request: { fields: { resourceSpans: { rule: 'repeated', type: 'ResourceSpans', id: 1 } } },
  } });
  const type = schema.lookupType('Request');
  const bytes = (value: string): Uint8Array => Buffer.from(value, 'hex');
  const raw = { resourceSpans: [{ scopeSpans: [{ spans: [{ traceId: bytes(trace), spanId: bytes(rootId), parentSpanId: bytes(chunkSpanId(chunk)), name: 'step build', startTimeUnixNano: '1700000000000000123', endTimeUnixNano: '1700000000000000456', attributes: [{ key: 'cost', value: { doubleValue: 1 } }, { key: 'nested', value: { kvlistValue: { values: [{ key: 'items', value: { arrayValue: { values: [{ stringValue: 'one' }, { intValue: '42' }, { bytesValue: bytes('beef') }] } } }] } } }], events: [{ name: 'later', timeUnixNano: '1700000000000000300', attributes: [{ key: 'a', value: { stringValue: 'b' } }] }, { name: 'earlier', timeUnixNano: '1700000000000000200' }], links: [{ traceId: bytes(trace), spanId: bytes(rootId), attributes: [{ key: 'blizzard.link.reason', value: { stringValue: 'next' } }] }], status: { code: 2, message: 'error' } }] }] }] };
  const sink = await startOtlpSink();
  let decoded: ReceivedSpan | undefined;
  try {
    const body = Buffer.from(type.encode(type.fromObject(raw)).finish());
    const status = await new Promise<number>((resolve, reject) => {
      const request = http.request(`${sink.endpoint}/v1/traces`, { method: 'POST', headers: { 'content-type': 'application/x-protobuf' } }, (response) => {
        response.resume();
        resolve(response.statusCode ?? 0);
      });
      request.once('error', reject);
      request.end(body);
    });
    expect(status).toBe(200);
    [decoded] = sink.spans();
  } finally {
    await sink.close();
  }
  expect(decoded?.spanId).toBe(rootId);
  expect(decoded?.parentSpanId).toBe(chunkSpanId(chunk));
  expect(decoded?.startTimeUnixNano).toBe('1700000000000000123');
  expect(decoded?.endTimeUnixNano).toBe('1700000000000000456');
  expect(decoded?.status).toEqual({ code: 2, message: 'error' });
  expect(decoded?.attributeTypes?.cost).toBe('double');
  expect(decoded?.attributes.nested).toEqual({ items: ['one', 42, 'beef'] });
  expect(decoded?.events.map((event) => event.name)).toEqual(['earlier', 'later']);
  expect(decoded?.events[1]?.attributes.a).toBe('b');
  expect(decoded?.links[0]?.spanId).toBe(rootId);
  expect(decoded?.links[0]?.attributes['blizzard.link.reason']).toBe('next');
});
