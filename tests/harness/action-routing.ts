import * as path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import type { TestHub } from './tenancy';

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
