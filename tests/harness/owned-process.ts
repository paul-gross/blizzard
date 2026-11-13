import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import * as fs from 'node:fs';

interface ProcessIdentity { pid: number; parent: number; started: string }

function identity(pid: number): ProcessIdentity | undefined {
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
    return { pid, parent: Number(fields[1]), started: fields[19]! };
  } catch {
    return undefined;
  }
}

/** Stop only descendants of the owned child, including descendants that created their own session. */
function killTree(root: number): void {
  const processes = fs.readdirSync('/proc').filter((name) => /^\d+$/.test(name))
    .map((name) => identity(Number(name))).filter((item): item is ProcessIdentity => item !== undefined);
  const owned = [root];
  for (let i = 0; i < owned.length; i++) {
    owned.push(...processes.filter((item) => item.parent === owned[i]).map((item) => item.pid));
  }
  for (const pid of owned.reverse()) {
    const recorded = processes.find((item) => item.pid === pid);
    if (recorded && identity(pid)?.started === recorded.started) {
      try { process.kill(pid, 'SIGKILL'); } catch { /* Already exited. */ }
    }
  }
}

/** Give the fixture a bounded graceful shutdown before killing its own process tree. */
export async function stopOwnedProcess(child: ChildProcessWithoutNullStreams, timeoutMs = 15_000): Promise<boolean> {
  if (child.exitCode !== null || child.signalCode !== null) return true;
  const exited = new Promise<boolean>((resolve) => child.once('exit', () => resolve(true)));
  let timer: NodeJS.Timeout;
  const graceful = await Promise.race([exited, new Promise<boolean>((resolve) => { timer = setTimeout(() => resolve(false), timeoutMs); })]);
  clearTimeout(timer!);
  if (!graceful && child.pid !== undefined) {
    killTree(child.pid);
    await Promise.race([exited, new Promise<void>((resolve) => setTimeout(resolve, 1000))]);
  }
  return graceful;
}
