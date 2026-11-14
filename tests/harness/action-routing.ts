import * as path from 'node:path';
import * as fs from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import type { TestHub, Tenant } from './tenancy';
import type { RunnerHost } from './runner-host';
import type { WorkerCheckpoint } from './worker-checkpoint';
import { stepUntilHeld } from './runner-steps';

/** Plant the invalid edge a graph validator cannot mint, on a test's own disposable hub only. */
export function breakActionEdge(hub: TestHub, graphId: string, node: string, target: string): void {
  if (!hub.fresh) throw new Error('breakActionEdge requires a fresh, test-owned hub');
  const db = new DatabaseSync(path.join(path.dirname(hub.coordinates.logPath), 'hub', 'data', 'hub.db'));
  try {
    const result = db.prepare(`UPDATE graph_edges SET to_node_name = 'nowhere'
      WHERE to_node_name = ? AND from_node_id IN
        (SELECT node_id FROM graph_nodes WHERE graph_id = ? AND name = ?)`).run(target, graphId, node);
    if (result.changes !== 1) throw new Error(`expected one edge, changed ${result.changes}`);
  } finally {
    db.close();
  }
}

/** Optional operator gate: the next act happens only after Enter on the controlling terminal. */
export function actionPause(instruction: string): void {
  if (process.env.BLIZZARD_ACTION_WALK !== 'manual') return;
  console.log(`\nACTION WALK STEP: ${instruction}\nPress Enter to continue.`);
  const tty = fs.openSync(process.env.BLIZZARD_ACTION_GATE_FIFO ?? '/dev/tty', 'r');
  try {
    const byte = Buffer.alloc(1);
    while (fs.readSync(tty, byte, 0, 1, null) === 1 && byte[0] !== 10) { /* one line */ }
  } finally {
    fs.closeSync(tty);
  }
}


/** Hold the mock worker only during an operator walk, before its build prompt runs. */
export function actionPrompt(prompt: string, checkpoint: WorkerCheckpoint): string {
  return process.env.BLIZZARD_ACTION_WALK === 'manual' ? checkpoint.snippet('action-build') + prompt : prompt;
}

/** Let an operator inspect the runner's real, open lease before releasing the mock worker. */
export async function actionLeaseGate(host: RunnerHost, chunk: string, checkpoint: WorkerCheckpoint, hub: TestHub, tenant: Tenant): Promise<void> {
  if (process.env.BLIZZARD_ACTION_WALK !== 'manual') return;
  const lease = await stepUntilHeld(host, chunk, checkpoint, 'action-build');
  const runner = host.runners['runner-local']!;
  const response = await runner.leasesForChunk(chunk);
  if (!response.ok || !response.body.items.some((item) => item.lease_id === lease.lease_id && item.state === 'running' && item.closed_at === null)) {
    throw new Error(`running lease readback failed for ${chunk}`);
  }
  console.log(`ACTION WALK LEASE ${JSON.stringify({ hub: hub.coordinates.url, tenant: tenant.id, chunk, board: `${hub.coordinates.url}/board/chunk/${chunk}`, runner: runner.id, lease: lease.lease_id, leasesUrl: `${host.url}/runners/${runner.id}/api/leases?chunk_id=${encodeURIComponent(chunk)}`, readback: response.body.items })}`);
  actionPause('Inspect the held build lease and matching board before releasing the worker');
  checkpoint.release('action-build');
}

/** Independent operator readbacks at a walk checkpoint; history and operational events retain their API order. */
export async function actionReadback(
  label: string, id: string, api: import('./client/clients').HubApi,
  cli: (args: string[]) => Promise<import('./cli').CliResult>,
  hub: TestHub, tenant: Tenant,
): Promise<void> {
  if (process.env.BLIZZARD_ACTION_WALK === 'manual') {
    console.log(`ACTION WALK INSPECT hub=${hub.coordinates.url} tenant=${tenant.id} board=${hub.coordinates.url}/board/chunk/${id} api=${hub.coordinates.url}/api/chunks/${id}`);
  }
  const detail = await api.get(`/api/chunks/${id}`);
  const events = await api.get(`/api/events?chunk_id=${encodeURIComponent(id)}`);
  if (detail.status !== 200 || events.status !== 200) throw new Error(`readback failed for ${id}`);
  const chunkCli = await cli(['hub', 'chunk', 'get', id, '--json']);
  const eventsCli = await cli(['hub', 'events', '--chunk', id, '--json']);
  if (chunkCli.code !== 0 || eventsCli.code !== 0) throw new Error(`CLI readback failed: ${chunkCli.stderr} ${eventsCli.stderr}`);
  const fromCli = JSON.parse(chunkCli.stdout);
  if (fromCli.chunk_id !== id || fromCli.status !== detail.body.status) throw new Error(`CLI/API chunk mismatch: ${chunkCli.stdout}`);
  const cliEvents = JSON.parse(eventsCli.stdout).events;
  const apiEvents = events.body.events;
  if (JSON.stringify(cliEvents) !== JSON.stringify(apiEvents)) throw new Error(`CLI/API event mismatch for ${id}`);
  const submissions = await api.get(`/api/chunks/${id}/flow-submissions`);
  if (submissions.status !== 200) throw new Error(`submission readback failed for ${id}`);
  const visits = [];
  for (const submission of submissions.body.items) {
    const turns = await api.get(`/api/chunks/${id}/flow-submissions/${submission.submission_id}/turns?limit=200`);
    if (turns.status !== 200) throw new Error(`turn readback failed for ${id}`);
    const records = [];
    for (const flowId of submission.flow_ids) {
      const record = await api.get(`/api/flows/${flowId}/record?limit=200`);
      const flowCli = await cli(['hub', 'flow', 'get', flowId, '--record', '--limit', '200', '--json']);
      if (record.status !== 200 || flowCli.code !== 0) throw new Error(`flow readback failed for ${flowId}: ${flowCli.stderr}`);
      const cliFacts = JSON.parse(flowCli.stdout).facts;
      // A live pending flow may gain turns between independent calls. Each fact already read must remain present.
      if (!record.body.facts.every((fact: { id: number }) => cliFacts.some((other: { id: number }) => fact.id === other.id))) {
        throw new Error(`CLI/API flow record mismatch for ${flowId}`);
      }
      records.push({ flowId, api: record.body.facts, cli: cliFacts });
    }
    visits.push({ submission, turns: turns.body.items, records });
  }
  const facts = await api.get(`/api/chunks/${id}/landing-facts`);
  if (facts.status !== 200) throw new Error(`fact readback failed for ${id}`);
  console.log(`ACTION WALK ${label} ${JSON.stringify({ chunk: id, graph: detail.body.graph_id, status: detail.body.status,
    node: detail.body.current_node_name, history: detail.body.history, escalation: detail.body.escalation, artifacts: detail.body.artifacts,
    bounces: detail.body.bounces, events: apiEvents, cliStatus: fromCli.status, cliEvents, visits, landingFacts: facts.body.items })}`);
}
