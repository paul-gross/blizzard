import * as fs from 'node:fs';
import * as path from 'node:path';
import { DuckDBInstance } from '@duckdb/node-api';
import { BLIZZARD_ROOT } from './env';

/** The datasets the reader loads. */
export const EGRESS_DATASETS = ['steps', 'invocations', 'events'] as const;
export type EgressDataset = (typeof EGRESS_DATASETS)[number];

/** The formats a hub exports; the config's default is `ndjson`. */
export type EgressFormat = 'ndjson' | 'parquet';

/** The docs' named recipes over a loaded dataset, by the marker the docs carry them under. */
export const EGRESS_RECIPES = {
  'cost-by-node-by-day': 'steps',
  'slowest-station-of-the-week': 'steps',
  'files-a-station-read-last-week': 'events',
} as const satisfies Record<string, EgressDataset>;
export type EgressRecipe = keyof typeof EGRESS_RECIPES;

/** One row as DuckDB's JSON rendering holds it: decimals and big integers as strings, times as text. */
export type EgressRow = Record<string, unknown>;

/** What a reader holds of a directory without loading a dataset: the text of its manifests and schema documents. */
export interface EgressDocuments {
  manifests: string[];
  schemas: string[];
}

/** Which of a directory's files a read binds the dataset to. */
export interface EgressQueryOptions {
  /** The live passes' files (`live`), a backfill's (`backfill`), or every file a manifest names (the default). */
  files?: 'live' | 'backfill';
}

/** One copy of a row in the shape both formats share: times as RFC 3339 in UTC, money as nine-place text, and `exported_at` kept. */
export type EgressCopy = Record<string, unknown>;

/** A fresh hub's export directory, read through the docs' own DuckDB recipes. */
export interface EgressReader {
  /** The export directory the hub writes (`${egress}` in `hubConfig`). */
  readonly directory: string;
  /**
   * Runs `sql` over `dataset` once an export manifest names it. `sql` reads the dictionary's views of it: for `steps`
   * and `invocations` `<dataset>_newest`, the newest copy of each row; for `events` `events_current` and
   * `events_by_version`; and `<dataset>` for every copy. Times are read in UTC.
   */
  query(dataset: EgressDataset, sql: string, options?: EgressQueryOptions): Promise<EgressRow[]>;
  /** One of the docs' named recipes, run as the docs give it. */
  recipe(name: EgressRecipe): Promise<EgressRow[]>;
  /**
   * Every copy of every row of `steps` or `invocations`, newest or not, in the shape the two formats share, so rows read
   * from NDJSON and from Parquet compare equal. A test picks the newest copy itself, by `exported_at`.
   */
  copies(dataset: 'steps' | 'invocations', options?: EgressQueryOptions): Promise<EgressCopy[]>;
  /** The same export directory's reader for another directory, and the format it holds. */
  at(directory: string, format: EgressFormat): EgressReader;
  /** Whether a manifest the directory holds now names a file of `dataset`; `query` waits for one, this asks once. */
  exports(dataset: EgressDataset): boolean;
  /** Every copy of every row of `dataset`, each rendered as one JSON document, for a scan that something is nowhere in the export. */
  rowTexts(dataset: EgressDataset): Promise<string[]>;
  /** The text of the manifests and schema documents the directory holds now. */
  documents(): EgressDocuments;
}

/** The format a hub's `[egress]` section exports, `ndjson` when it names none. */
export function configuredFormat(config: { egress?: unknown }): EgressFormat {
  const section = config.egress;
  const format = typeof section === 'object' && section !== null ? (section as { format?: unknown }).format : undefined;
  return format === 'parquet' ? 'parquet' : 'ndjson';
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

/** The dictionary: each dataset's columns, by name and type. */
const dictionary = JSON.parse(fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts', 'egress', 'dictionary.json'), 'utf8')) as {
  datasets: { name: string; columns: { name: string; type: string }[] }[];
};

/** A column in the shape both formats share: a time as RFC 3339 in UTC with microseconds, money as nine-place text. */
function canonicalColumn(name: string, type: string): string {
  if (type === 'timestamp') return `strftime(CAST(${name} AS TIMESTAMP), '%Y-%m-%dT%H:%M:%S.%fZ')`;
  if (type === 'money') return `CAST(${name} AS DECIMAL(38, 9))::VARCHAR`;
  return name;
}

/** The dictionary's views over a loaded dataset, by name. */
const VIEWS: Record<EgressDataset, string[]> = {
  steps: ['steps_newest'],
  invocations: ['invocations_newest'],
  events: ['events_current', 'events_by_version'],
};

/** The dictionary's definition of a view. */
function viewSql(view: string): string {
  return fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts', 'egress', `${view}.sql`), 'utf8').trim();
}

/** The docs' load recipe for a dataset in a format. */
function loadRecipe(dataset: EgressDataset, format: EgressFormat): string {
  if (format === 'parquet') return docRecipe('load-parquet');
  return docRecipe(dataset === 'events' ? 'load-events-ndjson' : 'load-ndjson');
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

/** The text of every `.json` file directly under `directory/sub`, in name order. */
function jsonTexts(directory: string, sub: string): string[] {
  const dir = path.join(directory, sub);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((name) => name.endsWith('.json'))
    .sort()
    .map((name) => fs.readFileSync(path.join(dir, name), 'utf8'));
}

/** Reads `directory` with an in-memory DuckDB per call, so each read sees the files the manifests name now. */
export function egressReader(directory: string, format: EgressFormat = 'ndjson'): EgressReader {
  const open = async (
    dataset: EgressDataset,
    files: EgressQueryOptions['files'],
    work: (connection: Awaited<ReturnType<DuckDBInstance['connect']>>) => Promise<EgressRow[]>,
  ): Promise<EgressRow[]> => {
    await waitForManifests(directory, dataset);
    const instance = await DuckDBInstance.create(':memory:');
    const connection = await instance.connect();
    try {
      await connection.run("SET TimeZone = 'UTC'");
      // The docs' load statements, a pair run in order; the dictionary's views wrap the relation they bind.
      const statements = loadRecipe(dataset, format).replaceAll('<directory>', directory).replaceAll('<dataset>', dataset).split(/;\s*\n/).filter((statement) => statement.trim() !== '');
      // The first statement sets the dataset's file list from the manifests; a backfill's files carry `backfill` in their names.
      for (const [position, statement] of statements.entries()) {
        await connection.run(statement);
        if (position === 0 && files !== undefined) {
          await connection.run(`SET VARIABLE ${dataset}_files = list_filter(getvariable('${dataset}_files'), f -> ${files === 'backfill' ? '' : 'NOT '}contains(f, 'backfill'))`);
        }
      }
      for (const view of VIEWS[dataset]) await connection.run(`CREATE VIEW ${view} AS ${viewSql(view)}`);
      return await work(connection);
    } finally {
      connection.closeSync();
      instance.closeSync();
    }
  };
  const query = (dataset: EgressDataset, sql: string, options: EgressQueryOptions = {}): Promise<EgressRow[]> =>
    open(dataset, options.files, async (connection) => (await connection.runAndReadAll(sql)).getRowObjectsJson() as EgressRow[]);
  return {
    directory,
    query,
    recipe: (name) => query(EGRESS_RECIPES[name], docRecipe(name)),
    copies: async (dataset, options = {}) => {
      const columns = (dictionary.datasets.find((entry) => entry.name === dataset)?.columns ?? []);
      const fields = columns.map(({ name, type }) => `'${name}': ${canonicalColumn(name, type)}`).join(', ');
      const rows = await query(dataset, `SELECT to_json({${fields}})::VARCHAR AS row FROM ${dataset}`, options);
      return rows.map((row) => JSON.parse(row.row as string) as EgressCopy);
    },
    at: (other, otherFormat) => egressReader(other, otherFormat),
    exports: (dataset) => manifestsName(directory, dataset),
    rowTexts: async (dataset) =>
      (await query(dataset, `SELECT to_json(t)::VARCHAR AS text FROM ${dataset} t`)).map((row) => row.text as string),
    documents: () => ({ manifests: jsonTexts(directory, '_manifests'), schemas: jsonTexts(directory, '_schema') }),
  };
}
