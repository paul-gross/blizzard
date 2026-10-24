import type { TestInfo } from '@playwright/test';

/** One call a client made: what it asked, what it was answered, and how long the answer took. */
export interface LatencyRecord {
  method: string;
  route: string;
  status: number;
  ms: number;
}

/** The name of the attachment carrying a test's records, which the reporter reads. */
export const LATENCY_ATTACHMENT = 'latency';

export type CallRecorder = (record: LatencyRecord) => void;

const records = new WeakMap<TestInfo, LatencyRecord[]>();

/** The recorder for the running test: every call it is handed lands in that test's records. */
export function recorderFor(testInfo: TestInfo): CallRecorder {
  return (record) => {
    const own = records.get(testInfo) ?? [];
    own.push(record);
    records.set(testInfo, own);
  };
}

/** The records the test has made so far. */
export function recordsOf(testInfo: TestInfo): readonly LatencyRecord[] {
  return records.get(testInfo) ?? [];
}

/** Times `call`, recording it as the call's answer arrives. `status` reads the answer's status, `0` when it threw. */
export async function timed<T>(
  record: CallRecorder,
  method: string,
  route: string,
  call: () => Promise<T>,
  status: (answer: T) => number,
): Promise<T> {
  const started = performance.now();
  let answered = 0;
  try {
    const answer = await call();
    answered = status(answer);
    return answer;
  } finally {
    record({ method, route, status: answered, ms: performance.now() - started });
  }
}
