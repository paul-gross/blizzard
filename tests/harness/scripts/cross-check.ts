import * as fs from 'node:fs';

// The isolation cross-check's comparison: two Playwright JSON reports, one run as declared and one with every test
// raised a level (BLIZZARD_ISOLATION_RAISE=1), must pass the same tests. Run as
// `node cross-check.ts <declared.json> <raised.json>`; it takes no import from the harness so Node runs it as is.

/** The annotation the `level` fixture records on each test it serves: the level the test declared. */
const LEVEL_ANNOTATION = 'isolation-level';

interface ReportTest {
  projectName?: string;
  status?: string;
  annotations?: { type: string; description?: string }[];
}
interface ReportSpec {
  title: string;
  file?: string;
  tests?: ReportTest[];
}
interface ReportSuite {
  title?: string;
  suites?: ReportSuite[];
  specs?: ReportSpec[];
}
interface Outcome {
  passed: boolean;
  level: string;
}

function read(file: string): ReportSuite {
  if (!fs.existsSync(file)) throw new Error(`report missing: ${file}`);
  return JSON.parse(fs.readFileSync(file, 'utf8')) as ReportSuite;
}

/** Every test in a report, keyed by project, file and title path. */
function outcomes(report: ReportSuite): Map<string, Outcome> {
  const found = new Map<string, Outcome>();
  const walk = (suite: ReportSuite, titles: string[]): void => {
    const here = suite.title ? [...titles, suite.title] : titles;
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) {
        const level = test.annotations?.find((annotation) => annotation.type === LEVEL_ANNOTATION)?.description ?? 'undeclared';
        found.set([test.projectName ?? '', ...here, spec.title].join(' › '), { passed: test.status === 'expected', level });
      }
    }
    for (const child of suite.suites ?? []) walk(child, here);
  };
  walk(report, []);
  return found;
}

/** The disagreements between the two reports, one line per test that passed in only one. */
export function disagreements(declared: ReportSuite, raised: ReportSuite): string[] {
  const before = outcomes(declared);
  const after = outcomes(raised);
  const lines: string[] = [];
  for (const [name, outcome] of before) {
    const other = after.get(name);
    if (outcome.passed && !other?.passed) lines.push(`${name} [declared ${outcome.level}] passed as declared, not when raised`);
    if (!outcome.passed && other?.passed) lines.push(`${name} [declared ${other.level}] passed when raised, not as declared`);
  }
  for (const [name, outcome] of after) {
    if (!before.has(name) && outcome.passed) lines.push(`${name} [declared ${outcome.level}] passed when raised, absent as declared`);
  }
  return lines;
}

function main(args: string[]): number {
  const [declaredFile, raisedFile] = args;
  if (!declaredFile || !raisedFile) {
    console.error('usage: cross-check.ts <declared.json> <raised.json>');
    return 2;
  }
  try {
    const lines = disagreements(read(declaredFile), read(raisedFile));
    if (lines.length === 0) {
      console.log('the declared and raised runs passed the same tests');
      return 0;
    }
    console.error(`${lines.length} test(s) passed in only one run:\n${lines.map((line) => `  ${line}`).join('\n')}`);
    return 1;
  } catch (error) {
    console.error((error as Error).message);
    return 1;
  }
}

if (process.argv[1] && import.meta.url === new URL(process.argv[1], 'file://').href) process.exit(main(process.argv.slice(2)));
