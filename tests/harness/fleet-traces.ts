import * as assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { BLIZZARD_ROOT } from './env';
import type { ReceivedSpan } from './otlp-sink';
import { chunkSpanId, chunkTraceId, lifetimeSpanId, lifetimeTraceId, spanId } from './trace-ids';

interface Attribute { name: string; type: string; on: string[]; optional: boolean; optional_on?: string[] }
interface SpanEntry { name: string; role: string; scope: string; service?: string }
interface Dictionary {
  attributes: Attribute[]; resource_attributes: { name: string }[]; spans: SpanEntry[];
  event_names: string[]; link_reasons: { name: string }[]; schema_version: string;
}
const dictionary = JSON.parse(fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts/traces/dictionary.json'), 'utf8')) as Dictionary;
const rootRoles = new Set(['step', 'gate']);
const chunkRoles = new Set(dictionary.spans.filter((entry) => entry.role === 'chunk' || entry.role.startsWith('chunk/')).map((entry) => entry.role));
const chunkKey = 'blizzard.chunk.id';
const nodeKey = 'blizzard.node.name';
const epochKey = 'blizzard.step.epoch';
const reasonKey = 'blizzard.link.reason';

export function roleOf(span: ReceivedSpan): string {
  if ('blizzard.step.kind' in span.attributes) return 'chunk/step';
  const entry = dictionary.spans.find((candidate) => {
    if (candidate.name === '<node>') return false;
    const marker = candidate.name.indexOf(' <');
    if (marker >= 0) return span.name.startsWith(candidate.name.slice(0, marker + 1)) || (candidate.role === 'runner/invocation' && span.name === 'invoke_agent');
    return candidate.name === span.name;
  });
  assert.ok(entry, `span name ${JSON.stringify(span.name)} is not in the dictionary`);
  return entry.role;
}

export function isFleet(span: ReceivedSpan): boolean {
  return dictionary.spans.some((entry) => entry.scope === span.scope.name);
}

function typeOf(value: unknown): string {
  if (Array.isArray(value)) return value.every((item) => typeof item === 'string') ? 'string[]' : 'invalid';
  if (typeof value === 'number') return Number.isInteger(value) ? 'int' : 'double';
  return typeof value === 'boolean' ? 'bool' : typeof value;
}

export function assertConforms(spans: readonly ReceivedSpan[]): void {
  const checkBag = (bag: Record<string, unknown>, types: Record<string, string> | undefined, role: string, where: string): void => {
    for (const [key, value] of Object.entries(bag)) {
      const entry = dictionary.attributes.find((item) => item.name === key);
      assert.ok(entry, `${where}: attribute ${key} is not in the dictionary`);
      assert.equal(types?.[key] ?? typeOf(value), entry.type, `${where}: ${key} has wrong type`);
      assert.ok(entry.on.includes(role), `${where}: ${key} is not published for ${role}`);
    }
    for (const entry of dictionary.attributes) {
      if (!entry.optional && entry.on.includes(role) && !entry.optional_on?.includes(role)) assert.ok(entry.name in bag, `${where}: lacks required ${entry.name}`);
    }
  };
  for (const span of spans) {
    const role = roleOf(span);
    const entry = dictionary.spans.find((item) => item.role === role)!;
    const where = `span ${span.name}`;
    checkBag(span.attributes, span.attributeTypes, role, where);
    assert.equal(span.scope.name, entry.scope, `${where}: scope`);
    for (const key of dictionary.resource_attributes) assert.ok(key.name in span.resource, `${where}: resource lacks ${key.name}`);
    assert.equal(span.resource['blizzard.trace.schema_version'], dictionary.schema_version, where);
    assert.ok([0, 2].includes(span.status.code), `${where}: status`);
    assert.equal(span.parentSpanId === null, ['chunk', 'chunk/lifetime'].includes(role), `${where}: root`);
    if (entry.service !== undefined) assert.equal(span.resource['service.name'], entry.service, where);
    else assert.notEqual(span.resource['service.name'], 'blizzard-chunk', where);
    for (const event of span.events) {
      assert.ok(dictionary.event_names.includes(event.name), `${where}: unknown event ${event.name}`);
      checkBag(event.attributes, event.attributeTypes, `event:${event.name}`, `${where} event ${event.name}`);
    }
    for (const link of span.links) {
      assert.ok(dictionary.link_reasons.some((item) => item.name === link.attributes[reasonKey]), `${where}: unknown link reason`);
      checkBag(link.attributes, link.attributeTypes, 'link', `${where} link`);
    }
  }
}

export interface StepTrace { root: ReceivedSpan; children: ReceivedSpan[] }
const time = (span: ReceivedSpan, field: 'startTimeUnixNano' | 'endTimeUnixNano'): bigint => BigInt(span[field]);
const identity = (span: ReceivedSpan): string => `${span.traceId}/${span.spanId}`;
const parent = (span: ReceivedSpan): string => `${span.traceId}/${span.parentSpanId ?? ''}`;
const runner = (span: ReceivedSpan): boolean => roleOf(span).startsWith('runner/');

export function tracesOf(spans: readonly ReceivedSpan[]): StepTrace[] {
  const roots = new Map(spans.filter((span) => rootRoles.has(roleOf(span))).map((root) => [identity(root), { root, children: [] as ReceivedSpan[] }]));
  for (const span of spans) {
    const role = roleOf(span);
    if (runner(span) || rootRoles.has(role) || chunkRoles.has(role)) continue;
    const trace = roots.get(parent(span));
    assert.ok(trace, `${span.name}: no exported step root`);
    trace.children.push(span);
  }
  return [...roots.values()].sort((a, b) => time(a.root, 'startTimeUnixNano') < time(b.root, 'startTimeUnixNano') ? -1 : time(a.root, 'startTimeUnixNano') > time(b.root, 'startTimeUnixNano') ? 1 : a.root.name.localeCompare(b.root.name));
}

export function assertIdentity(traces: readonly StepTrace[], options: { decisionIds?: readonly string[] } = {}): void {
  for (const { root, children } of traces) {
    const chunkId = root.attributes[chunkKey] as string;
    const epoch = root.attributes[epochKey] as number;
    const role = roleOf(root);
    const ids = role === 'gate' ? (options.decisionIds ?? []).map((decisionId) => spanId({ chunkId, epoch, decisionId }, role)) : [spanId({ chunkId, epoch }, role)];
    assert.ok(ids.includes(root.spanId), `${root.name}: span id does not derive from its step`);
    assert.equal(root.traceId, chunkTraceId(chunkId), `${root.name}: trace id`);
    assert.equal(root.parentSpanId, chunkSpanId(chunkId), `${root.name}: parent`);
    for (const child of children) assert.equal(parent(child), identity(root), child.name);
  }
  const ids = new Set(traces.map(({ root }) => root.spanId));
  for (const { root } of traces) for (const link of root.links) assert.ok(ids.has(link.spanId), `${root.name}: link target is not an exported root`);
}

export interface StepExpect {
  name: string; outcome: string; toNode?: string | null; status?: number; children?: readonly string[]; events?: readonly string[];
  minEvents?: Readonly<Record<string, number>>; minChildren?: Readonly<Record<string, number>>; link?: string | null;
  attributes?: Readonly<Record<string, unknown>>; childAttributes?: readonly { name: string; key: string; value: unknown }[];
}
const sorted = (values: readonly string[]): string[] => [...values].sort();
function counts(actual: string[], exact: readonly string[], minimum: Readonly<Record<string, number>>, where: string): void {
  assert.deepEqual(sorted(actual.filter((name) => !(name in minimum))), sorted(exact), where);
  for (const [name, count] of Object.entries(minimum)) assert.ok(actual.filter((item) => item === name).length >= count, `${where}: expected ${count} ${name}`);
}
export function assertSkeleton(traces: readonly StepTrace[], expected: readonly StepExpect[]): void {
  assert.deepEqual(traces.slice(0, expected.length).map((trace) => trace.root.name), expected.map((item) => item.name), 'step roots');
  expected.forEach((want, index) => {
    const { root, children } = traces[index]!;
    const where = want.name;
    assert.equal(root.attributes['blizzard.step.outcome'], want.outcome, where);
    assert.equal(root.attributes['blizzard.step.to_node.name'] ?? null, want.toNode ?? null, where);
    assert.equal(root.status.code, want.status ?? 0, where);
    counts(children.map((child) => child.name), want.children ?? [], want.minChildren ?? {}, `${where}: children`);
    counts(root.events.map((event) => event.name).filter((name) => name !== 'invocation'), want.events ?? [], want.minEvents ?? {}, `${where}: events`);
    assert.deepEqual(root.links.map((link) => link.attributes[reasonKey]), want.link ? [want.link] : [], `${where}: links`);
    for (const [key, value] of Object.entries(want.attributes ?? {})) assert.deepEqual(root.attributes[key], value, `${where}: ${key}`);
    for (const item of want.childAttributes ?? []) {
      const matches = children.filter((child) => child.name === item.name);
      assert.equal(matches.length, 1, `${where}: ${item.name}`);
      assert.deepEqual(matches[0]!.attributes[item.key], item.value, `${where}: ${item.name} ${item.key}`);
    }
  });
}

export function assertChunkTrace(spans: readonly ReceivedSpan[], chunkId: string, options: { outcome: string; steps: number; waits?: readonly string[] }): { work: ReceivedSpan; lifetime: ReceivedSpan } {
  const workTrace = chunkTraceId(chunkId);
  const lifeTrace = lifetimeTraceId(chunkId);
  const mine = spans.filter((span) => span.attributes[chunkKey] === chunkId);
  assert.deepEqual(sorted([...new Set(mine.map((span) => span.traceId))]), sorted([workTrace, lifeTrace]), 'chunk trace ids');
  const workSpans = mine.filter((span) => span.traceId === workTrace);
  const lifeSpans = mine.filter((span) => span.traceId === lifeTrace);
  const one = (items: ReceivedSpan[], role: string): ReceivedSpan => {
    const matches = items.filter((span) => roleOf(span) === role);
    assert.equal(matches.length, 1, `expected one ${role}`);
    return matches[0]!;
  };
  const work = one(workSpans, 'chunk');
  const life = one(lifeSpans, 'chunk/lifetime');
  assert.deepEqual([work.name, work.spanId, work.parentSpanId], ['chunk work', chunkSpanId(chunkId), null]);
  assert.deepEqual([life.name, life.spanId, life.parentSpanId], ['chunk', lifetimeSpanId(chunkId), null]);
  for (const root of [work, life]) {
    assert.equal(root.attributes['blizzard.chunk.outcome'], options.outcome);
    assert.equal(root.attributes['blizzard.chunk.steps'], options.steps);
  }
  assert.ok(workSpans.every((span) => !chunkRoles.has(roleOf(span)) || span === work), 'chunk-level spans in work');
  const roots = workSpans.filter((span) => rootRoles.has(roleOf(span)));
  assert.equal(roots.length, options.steps);
  assert.ok(roots.every((root) => root.parentSpanId === work.spanId));
  assert.ok(roots.length > 0, 'chunk has no step roots');
  assert.equal(time(work, 'startTimeUnixNano'), roots.reduce((min, root) => time(root, 'startTimeUnixNano') < min ? time(root, 'startTimeUnixNano') : min, time(roots[0]!, 'startTimeUnixNano')));
  assert.ok(roots.every((root) => time(work, 'endTimeUnixNano') >= time(root, 'endTimeUnixNano')));
  assert.deepEqual(work.links.map((link) => [link.traceId, link.spanId, link.attributes[reasonKey]]), [[lifeTrace, life.spanId, 'lifetime']]);
  assert.ok(time(life, 'startTimeUnixNano') <= time(work, 'startTimeUnixNano'));
  assert.equal(time(life, 'endTimeUnixNano'), time(work, 'endTimeUnixNano'));
  assert.ok(lifeSpans.every((span) => span.resource['service.name'] === 'blizzard-chunk'));
  assert.ok(workSpans.every((span) => span.resource['service.name'] !== 'blizzard-chunk'));
  const told = lifeSpans.filter((span) => roleOf(span) === 'chunk/step');
  assert.equal(told.length, options.steps);
  const targets = new Set<string>();
  for (const step of told) {
    assert.equal(step.parentSpanId, life.spanId);
    assert.equal(step.links.length, 1);
    const link = step.links[0]!;
    const target = roots.find((root) => root.spanId === link.spanId);
    assert.ok(target, `${step.name}: no work root`);
    assert.equal(link.traceId, workTrace);
    assert.equal(link.attributes[reasonKey], 'work');
    targets.add(target.spanId);
    const owned = workSpans.filter((child) => child.parentSpanId === target.spanId);
    const before = owned.filter((child) => ['queue', 'claim'].includes(roleOf(child)));
    const pickup = owned.filter((child) => roleOf(child) === 'pickup');
    const earliest = [target, ...before].reduce((min, span) => time(span, 'startTimeUnixNano') < min ? time(span, 'startTimeUnixNano') : min, time(target, 'startTimeUnixNano'));
    const latest = [target, ...pickup].reduce((max, span) => time(span, 'endTimeUnixNano') > max ? time(span, 'endTimeUnixNano') : max, time(target, 'endTimeUnixNano'));
    assert.equal(step.name, target.attributes[nodeKey]);
    assert.ok(earliest <= time(step, 'startTimeUnixNano') && time(step, 'startTimeUnixNano') <= time(target, 'startTimeUnixNano'));
    assert.ok(time(target, 'endTimeUnixNano') <= time(step, 'endTimeUnixNano') && time(step, 'endTimeUnixNano') <= latest);
    for (const key of ['blizzard.step.wait.queue_ms', 'blizzard.step.wait.claim_ms', 'blizzard.step.wait.ask_ms', 'blizzard.step.wait.pause_ms', 'blizzard.step.wait.pickup_ms', epochKey, 'blizzard.step.outcome']) {
      assert.ok(key in step.attributes && key in target.attributes, `${step.name}: missing ${key}`);
      assert.deepEqual(step.attributes[key], target.attributes[key], `${step.name}: ${key}`);
    }
    assert.equal(step.attributes['blizzard.step.kind'], roleOf(target) === 'gate' ? 'gate' : 'step');
  }
  assert.deepEqual(sorted([...targets]), sorted(roots.map((root) => root.spanId)), 'a step root has no lifetime span');
  const waited = lifeSpans.filter((span) => !['chunk/lifetime', 'chunk/step', 'chunk/completed'].includes(roleOf(span))).map((span) => span.name);
  assert.deepEqual(sorted(waited), sorted(options.waits ?? ['backlog wait']), 'chunk waits');
  const covered = lifeSpans.filter((span) => span !== life).sort((a, b) => time(a, 'startTimeUnixNano') < time(b, 'startTimeUnixNano') ? -1 : time(a, 'startTimeUnixNano') > time(b, 'startTimeUnixNano') ? 1 : 0);
  assert.ok(covered.every((span) => span.parentSpanId === life.spanId));
  for (let index = 1; index < covered.length; index++) assert.ok(time(covered[index - 1]!, 'endTimeUnixNano') <= time(covered[index]!, 'startTimeUnixNano'), 'lifetime spans overlap');
  return { work, lifetime: life };
}

export function assertRunnerNesting(spans: readonly ReceivedSpan[], workers: number): ReceivedSpan[] {
  const roots = new Set(spans.filter((span) => roleOf(span) === 'step').map(identity));
  const runners = spans.filter(runner);
  const workerSpans = runners.filter((span) => roleOf(span) === 'runner/worker');
  const workerIds = new Set(workerSpans.map(identity));
  assert.equal(workerIds.size, workers, 'runner worker count');
  for (const span of runners) {
    assert.equal(span.traceId, chunkTraceId(span.attributes[chunkKey] as string), `${span.name}: trace`);
    assert.ok((roleOf(span) === 'runner/worker' ? roots : workerIds).has(parent(span)), `${span.name}: parent`);
  }
  for (const worker of workerSpans) assert.ok(runners.some((span) => roleOf(span) === 'runner/invocation' && parent(span) === identity(worker)), `${worker.name}: no invocation`);
  return runners;
}

export interface InvocationExpect { harnessId: string; responseModel: string; requestModel?: string }
export function assertInvocations(spans: readonly ReceivedSpan[], expected: Readonly<Record<string, InvocationExpect>>): void {
  const invocations = spans.filter((span) => roleOf(span) === 'runner/invocation');
  assert.deepEqual(sorted([...new Set(invocations.map((span) => span.attributes[nodeKey] as string))]), sorted(Object.keys(expected)), 'invocation nodes');
  for (const span of invocations) {
    const attrs = span.attributes;
    const want = expected[attrs[nodeKey] as string]!;
    assert.equal(attrs['gen_ai.operation.name'], 'invoke_agent');
    assert.equal(attrs['blizzard.harness.id'], want.harnessId);
    assert.equal(attrs['gen_ai.response.model'], want.responseModel);
    if (want.requestModel !== undefined) assert.equal(attrs['gen_ai.request.model'], want.requestModel);
    for (const key of ['gen_ai.usage.input_tokens', 'gen_ai.usage.output_tokens']) assert.ok(typeof attrs[key] === 'number' && attrs[key] > 0, `${span.name}: ${key}`);
  }
}

export function assertPlatformNesting(fleet: readonly ReceivedSpan[], platform: readonly ReceivedSpan[], chained = 'runner work-items'): void {
  const byId = new Map([...fleet, ...platform].map((span) => [identity(span), span]));
  const roots = new Set(fleet.filter((span) => roleOf(span) === 'step').map(identity));
  const until = new Map<string, bigint>();
  for (const span of fleet.filter((item) => rootRoles.has(roleOf(item)))) {
    const end = time(span, 'endTimeUnixNano');
    if (end > (until.get(span.traceId) ?? 0n)) until.set(span.traceId, end);
  }
  const children = (span: ReceivedSpan, scope?: string, service?: string): ReceivedSpan[] => platform.filter((item) => parent(item) === identity(span) && (scope === undefined || item.scope.name === scope) && (service === undefined || item.resource['service.name'] === service));
  const descendants = (span: ReceivedSpan): ReceivedSpan[] => children(span).flatMap((child) => [child, ...descendants(child)]);
  const commands = platform.filter((span) => span.scope.name === 'blizzard.cli' && time(span, 'startTimeUnixNano') < (until.get(span.traceId) ?? time(span, 'startTimeUnixNano') + 1n));
  assert.ok(commands.length, 'no worker command span');
  let chains = 0;
  for (const command of commands) {
    const name = command.attributes['blizzard.cli.command'];
    assert.equal(command.traceId, chunkTraceId(String(command.attributes[chunkKey])), `command ${String(name)}: trace`);
    let node = command;
    const seen = new Set<string>();
    while (!roots.has(identity(node))) {
      assert.ok(!seen.has(identity(node)), `command ${String(name)}: cyclic ancestry`);
      seen.add(identity(node));
      assert.ok(node.parentSpanId !== null, `command ${String(name)}: ancestry ends at ${node.name}`);
      const next = byId.get(parent(node));
      assert.ok(next, `command ${String(name)}: missing ancestor ${node.parentSpanId}`);
      node = next;
    }
    const servers = children(command, 'fastapi', 'blizzard-runner');
    assert.ok(servers.length, `command ${String(name)}: no runner request`);
    if (name !== chained) continue;
    const clients = servers.flatMap((server) => children(server, 'opentelemetry.instrumentation.httpx', 'blizzard-runner'));
    assert.ok(clients.length, `command ${String(name)}: no hub client`);
    const hub = clients.flatMap((client) => children(client, 'fastapi', 'blizzard-hub'));
    assert.ok(hub.length, `command ${String(name)}: no hub request`);
    assert.ok(hub.some((request) => descendants(request).some((span) => span.scope.name === 'opentelemetry.instrumentation.sqlalchemy')), `command ${String(name)}: no query`);
    chains++;
  }
  assert.ok(chains, `no ${chained} command span`);
}

export function assertNoLeaks(spans: readonly ReceivedSpan[], planted: Readonly<Record<string, readonly string[]>>): void {
  assert.ok(spans.length, 'no exported spans');
  const strings = (value: unknown): string[] => {
    if (typeof value === 'string') return [value];
    if (Array.isArray(value)) return value.flatMap(strings);
    if (value !== null && typeof value === 'object') return Object.entries(value).flatMap(([key, item]) => [key, ...strings(item)]);
    return [];
  };
  const valuesInExport = strings(spans);
  for (const [kind, values] of Object.entries(planted)) {
    assert.ok(values.length && values.every(Boolean), `nothing was planted for ${kind}`);
    for (const value of values) assert.ok(!valuesInExport.some((item) => item.includes(value)), `export carries a planted ${kind}`);
  }
}
