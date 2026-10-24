import * as fs from 'node:fs';
import * as path from 'node:path';
import type { FullConfig, FullResult, Reporter, TestCase, TestResult } from '@playwright/test/reporter';
import { LATENCY_ATTACHMENT, type LatencyRecord } from './client/latency';
import { runDir } from './env';

/** A call over this long counts toward the summary's slow total. */
const SLOW_MS = 1000;
const SLOWEST_ROUTES = 10;

export interface RouteLatency {
  route: string;
  calls: number;
  maxMs: number;
  totalMs: number;
}

export interface LatencySummary {
  calls: number;
  callsOverOneSecond: number;
  slowestWrite: (LatencyRecord & { test: string }) | null;
  slowestRoutes: RouteLatency[];
}

/** A route with its ids collapsed, so calls on different records of one kind are one route. */
function routeKey(record: LatencyRecord): string {
  return `${record.method} ${record.route.replace(/\b[a-z]{2,4}_[0-9A-Za-z]{20,}\b/g, ':id')}`;
}

/** The run's latency: its slowest write, how many calls passed a second, and the routes that took longest. */
export function summarize(calls: { test: string; record: LatencyRecord }[]): LatencySummary {
  const writes = calls.filter(({ record }) => record.method !== 'GET');
  const slowest = writes.reduce<(typeof calls)[number] | null>((found, call) => (found === null || call.record.ms > found.record.ms ? call : found), null);
  const byRoute = new Map<string, RouteLatency>();
  for (const { record } of calls) {
    const route = routeKey(record);
    const entry = byRoute.get(route) ?? { route, calls: 0, maxMs: 0, totalMs: 0 };
    entry.calls += 1;
    entry.maxMs = Math.max(entry.maxMs, record.ms);
    entry.totalMs += record.ms;
    byRoute.set(route, entry);
  }
  return {
    calls: calls.length,
    callsOverOneSecond: calls.filter(({ record }) => record.ms > SLOW_MS).length,
    slowestWrite: slowest === null ? null : { ...slowest.record, test: slowest.test },
    slowestRoutes: [...byRoute.values()].sort((a, b) => b.totalMs - a.totalMs).slice(0, SLOWEST_ROUTES),
  };
}

/**
 * Collects the latency records each test attached and writes the run's summary to `latency-summary.json` in the output
 * directory, where CI keeps it beside the json report. Removes the run's directory once the run passed with `CI` unset,
 * so a red run's logs are still on disk to read.
 */
export default class RunDirectoryReporter implements Reporter {
  private outputDir = 'test-results';
  private readonly calls: { test: string; record: LatencyRecord }[] = [];

  onBegin(config: FullConfig): void {
    this.outputDir = config.projects[0]?.outputDir ?? this.outputDir;
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    for (const attachment of result.attachments) {
      if (attachment.name !== LATENCY_ATTACHMENT || attachment.body === undefined) continue;
      for (const record of JSON.parse(attachment.body.toString()) as LatencyRecord[]) this.calls.push({ test: test.titlePath().slice(3).join(' › '), record });
    }
  }

  onEnd(result: FullResult): void {
    fs.mkdirSync(this.outputDir, { recursive: true });
    fs.writeFileSync(path.join(this.outputDir, 'latency-summary.json'), `${JSON.stringify(summarize(this.calls), null, 2)}\n`);
    if (result.status !== 'passed' || process.env.CI) return;
    fs.rmSync(runDir(), { recursive: true, force: true });
  }
}
