import * as fs from 'node:fs';
import { readContract } from './env';

/** The hub's event for an exception no handler maps; its line names the request that caused it. */
export const UNHANDLED_REQUEST_ERROR = 'unhandled request error';

export interface LogLine {
  raw: string;
  /** The parsed JSON object, or `undefined` for a line that is not one (uvicorn's own notices and tracebacks). */
  json: Record<string, unknown> | undefined;
}

export function parseLine(raw: string): LogLine {
  if (!raw.startsWith('{')) return { raw, json: undefined };
  try {
    const parsed: unknown = JSON.parse(raw);
    const isObject = typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed);
    return { raw, json: isObject ? (parsed as Record<string, unknown>) : undefined };
  } catch {
    return { raw, json: undefined };
  }
}

export function parseLines(text: string): LogLine[] {
  return text
    .split('\n')
    .filter((line) => line !== '')
    .map(parseLine);
}

/** Reads a log incrementally: each `read` returns the lines written since the last. */
export class LogReader {
  private offset = 0;

  constructor(readonly path: string) {}

  read(): LogLine[] {
    let size: number;
    try {
      size = fs.statSync(this.path).size;
    } catch {
      return [];
    }
    if (size <= this.offset) return [];
    const descriptor = fs.openSync(this.path, 'r');
    try {
      const buffer = Buffer.alloc(size - this.offset);
      fs.readSync(descriptor, buffer, 0, buffer.length, this.offset);
      // A line still being written is left for the next read.
      const text = buffer.toString('utf8');
      const complete = text.lastIndexOf('\n') + 1;
      this.offset += Buffer.byteLength(text.slice(0, complete));
      return parseLines(text.slice(0, complete));
    } finally {
      fs.closeSync(descriptor);
    }
  }
}

/** A JSON line a tenant-bound request produced carries its `tenant_id`. */
export function forTenant(lines: LogLine[], tenantId: string): LogLine[] {
  return lines.filter((line) => line.json?.tenant_id === tenantId);
}

/** The lines no tenant claimed: a hub-wide read. */
export function untagged(lines: LogLine[]): LogLine[] {
  return lines.filter((line) => line.json !== undefined && line.json.tenant_id === undefined);
}

/**
 * The lines a statement guard refused outside a request. A refusal inside a request answers `500` and the hub logs
 * its `unhandled request error` line; the client has already failed the test that sent it, so the scan passes over
 * that line, and over non-JSON lines such as uvicorn's re-raised traceback. Any other JSON line naming a class the
 * environment contract lists as a refusal comes from a background pass and fails the run.
 */
export function findBackgroundRefusals(lines: LogLine[], refusals: string[] = readContract().refusals): LogLine[] {
  return lines.filter(
    (line) =>
      line.json !== undefined &&
      line.json.event !== UNHANDLED_REQUEST_ERROR &&
      refusals.some((refusal) => line.raw.includes(refusal)),
  );
}

/** Reads a whole log and returns its background refusals. */
export function scanLog(logPath: string, refusals?: string[]): LogLine[] {
  const text = fs.existsSync(logPath) ? fs.readFileSync(logPath, 'utf8') : '';
  return findBackgroundRefusals(parseLines(text), refusals);
}
