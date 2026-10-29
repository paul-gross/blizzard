import * as fs from 'node:fs';
import * as path from 'node:path';
import { DuckDBInstance } from '@duckdb/node-api';
import { BLIZZARD_ROOT } from './env';

/** The datasets the reader loads; `events` is read by NDJSON through its own recipe, which the reader does not carry yet. */
export const EGRESS_DATASETS = ['steps', 'invocations'] as const;
export type EgressDataset = (typeof EGRESS_DATASETS)[number];

/** The docs' named recipes over a loaded dataset, by the marker the docs carry them under. */
export const EGRESS_RECIPES = {
  'cost-by-node-by-day': 'steps',
  'slowest-station-of-the-week': 'steps',
} as const satisfies Record<string, EgressDataset>;
export type EgressRecipe = keyof typeof EGRESS_RECIPES;

/** One row as DuckDB's JSON rendering holds it: decimals and big integers as strings, times as text. */
export type EgressRow = Record<string, unknown>;

/** A fresh hub's export directory, read through the docs' own DuckDB recipes. */
export interface EgressReader {
  /** The export directory the hub writes (`${egress}` in `hubConfig`). */
  readonly directory: string;
  /**
   * Runs `sql` over `dataset` once an export manifest names it. `sql` reads `<dataset>_newest`, the dictionary's view
   * of the newest copy of each row, or `<dataset>` for every copy.
   */
  query(dataset: EgressDataset, sql: string): Promise<EgressRow[]>;
  /** One of the docs' named recipes, run as the docs give it. */
  recipe(name: EgressRecipe): Promise<EgressRow[]>;
}

const DOC = path.join(BLIZZARD_ROOT, 'docs', 'deployment', 'egress.md');
const WAIT_MS = 15_000;
const POLL_MS = 100;

/** The export directory of the stack made in `dir`. */
export function egressDirectory(dir: string): string {
  return path.join(dir, 'egress');
}

/** The SQL of a recipe under the docs' `### DuckDB`, found by its marker. */
export function docRecipe(name: string): string {
  const section = fs.readFileSync(DOC, 'utf8').split('### DuckDB', 2)[1]?.split('\n### ', 1)[0] ?? '';
  const match = new RegExp(`<!-- recipe:${name} -->\\n\\n\`\`\`sql\\n([\\s\\S]*?)\\n\`\`\``).exec(section);
  if (match === null) throw new Error(`no recipe ${name} under ### DuckDB in docs/deployment/egress.md`);
  return match[1] as string;
}

/** The dictionary's view of a dataset's newest copies. */
function newestView(dataset: EgressDataset): string {
  return fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts', 'egress', `${dataset}_newest.sql`), 'utf8').trim();
}

/** Whether some manifest under `directory` names a file of `dataset`. */
function manifestsName(directory: string, dataset: string): boolean {
  const manifests = path.join(directory, '_manifests');
  if (!fs.existsSync(manifests)) return false;
  return fs
    .readdirSync(manifests)
    .filter((name) => name.endsWith('.json'))
    .some((name) => {
      const manifest = JSON.parse(fs.readFileSync(path.join(manifests, name), 'utf8')) as { files?: { dataset: string }[] };
      return (manifest.files ?? []).some((file) => file.dataset === dataset);
    });
}

async function waitForManifests(directory: string, dataset: string): Promise<void> {
  const deadline = Date.now() + WAIT_MS;
  while (!manifestsName(directory, dataset)) {
    if (Date.now() > deadline) throw new Error(`no export manifest under ${directory}/_manifests names ${dataset} within ${WAIT_MS / 1000}s; has an egress pass run?`);
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

/** Reads `directory` with an in-memory DuckDB per call, so each read sees the files the manifests name now. */
export function egressReader(directory: string): EgressReader {
  const query = async (dataset: EgressDataset, sql: string): Promise<EgressRow[]> => {
    await waitForManifests(directory, dataset);
    const instance = await DuckDBInstance.create(':memory:');
    const connection = await instance.connect();
    try {
      // The docs' load statements, a pair run in order; the dictionary's view wraps the relation they bind.
      for (const statement of docRecipe('load-parquet').replaceAll('<directory>', directory).replaceAll('<dataset>', dataset).split(/;\s*\n/)) {
        if (statement.trim() !== '') await connection.run(statement);
      }
      await connection.run(`CREATE VIEW ${dataset}_newest AS ${newestView(dataset)}`);
      const result = await connection.runAndReadAll(sql);
      return result.getRowObjectsJson() as EgressRow[];
    } finally {
      connection.closeSync();
      instance.closeSync();
    }
  };
  return {
    directory,
    query,
    recipe: (name) => query(EGRESS_RECIPES[name], docRecipe(name)),
  };
}
