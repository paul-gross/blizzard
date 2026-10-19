// @ts-check
/*
 * The story render check — the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:story-render` method, last step of `npm run story-render` (from web/).
 *
 * Serves the static workshop build in `dist/storybook` and opens every story in headless
 * Chromium. Storybook's preview announces each story's outcome on its channel, and a story
 * is rendered only when the channel says `storyRendered`, which the preview emits after
 * Angular has bootstrapped the story and run its first change detection. The body's
 * `sb-show-main` class is not a verdict: the framework sets it before Angular renders.
 *
 * A story fails when:
 *   - the channel announces `storyErrored`, `storyThrewException` or `storyMissing`,
 *   - the page raises an uncaught exception,
 *   - the page logs through `console.error` (Angular reports a template error in a story's
 *     first render this way, while still announcing a render),
 *   - a request for one of the workshop's own files fails, or
 *   - no verdict arrives inside the bound.
 *
 * It also fails when the index holds no story, since an empty workshop passing would be a
 * check that cannot fail, and it proves it can fail before it opens any story: from the same
 * server it serves planted pages that speak the preview's channel, and refuses to run when it
 * misjudges one.
 *
 * Judges that a story renders, never what it looks like: no pixels, no DOM queries, no `play`.
 *
 *   node scripts/story-render-check.js [build-dir]   (default: dist/storybook)
 */

const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');

const DEFAULT_BUILD_DIR = path.join(__dirname, '..', 'dist', 'storybook');

/** How long a story has to announce its verdict. */
const VERDICT_BOUND_MS = 10_000;

/** The self-test's bound: its planted pages announce at once, or never. */
const SELF_TEST_BOUND_MS = 1_500;

/** How long the page stays watched after its verdict, for what a render logs just after announcing. */
const SETTLE_MS = 150;

/** How much of a failure's message is printed. */
const MESSAGE_LIMIT = 400;

const CHANNEL_GLOBAL = '__STORYBOOK_ADDONS_CHANNEL__';

/** The channel events that are a verdict, and whether each is a render. */
const VERDICT_EVENTS = {
  storyRendered: true,
  storyErrored: false,
  storyThrewException: false,
  storyMissing: false,
};

/**
 * Runs in every page before its own scripts. The preview assigns the channel global once at
 * startup, so the hook catches that assignment and subscribes before the first event; polling
 * for it would race `storyRendered` on a fast story.
 */
const INIT_SCRIPT = `(() => {
  const verdictEvents = ${JSON.stringify(Object.keys(VERDICT_EVENTS))};
  const describe = (payload) => {
    try {
      if (payload instanceof Error) return payload.message;
      return typeof payload === 'string' ? payload : JSON.stringify(payload);
    } catch {
      return String(payload);
    }
  };
  let channel;
  Object.defineProperty(globalThis, ${JSON.stringify(CHANNEL_GLOBAL)}, {
    configurable: true,
    get: () => channel,
    set: (value) => {
      channel = value;
      for (const event of verdictEvents) {
        value.on(event, (...args) => {
          if (!globalThis.__bzVerdict) globalThis.__bzVerdict = { event, detail: args.map(describe).join(' ') };
        });
      }
    },
  });
})();`;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.png': 'image/png',
  '.map': 'application/json; charset=utf-8',
};

/**
 * A page that speaks the preview's channel through the same global the preview assigns, with
 * an object that has `.on` and `.emit`, then runs `body`.
 * @param {string} body
 */
const plantedPage = (body) => `<!doctype html><meta charset="utf-8"><title>planted</title><script>
const handlers = {};
globalThis.${CHANNEL_GLOBAL} = {
  on(event, fn) { (handlers[event] ??= []).push(fn); },
  emit(event, ...args) { (handlers[event] ?? []).forEach((fn) => fn(...args)); },
};
const emit = (event, ...args) => globalThis.${CHANNEL_GLOBAL}.emit(event, ...args);
${body}
</script>`;

/** The self-test's planted pages, each with whether the check must judge it a render. */
const PLANTED_PAGES = [
  { name: 'renders-cleanly', passes: true, body: "setTimeout(() => emit('storyRendered', 'x'), 0);" },
  {
    name: 'announces-errored',
    passes: false,
    body: "setTimeout(() => emit('storyErrored', { title: 'planted', description: 'failed' }), 0);",
  },
  {
    name: 'renders-then-logs-an-error',
    passes: false,
    body: "setTimeout(() => { emit('storyRendered', 'x'); console.error('NG0303: planted template error'); }, 0);",
  },
  {
    name: 'raises-an-exception',
    passes: false,
    body: "setTimeout(() => { emit('storyRendered', 'x'); setTimeout(() => { throw new Error('planted'); }, 20); }, 0);",
  },
  { name: 'never-announces', passes: false, body: '/* silence */' },
];

const SELF_TEST_PREFIX = '/__self-test__/';

/** A failure's message, clipped: a stack or a dump adds length, not information.
 * @param {string} message
 */
const clip = (message) => (message.length > MESSAGE_LIMIT ? `${message.slice(0, MESSAGE_LIMIT)}…` : message);

/**
 * Serve `dir` and the in-memory `pages` on an ephemeral 127.0.0.1 port.
 * @param {string} dir absolute
 * @param {Map<string, string>} pages URL path → html
 * @returns {Promise<{ origin: string, close: () => Promise<void> }>}
 */
function serve(dir, pages) {
  const root = path.resolve(dir);
  const server = http.createServer((req, res) => {
    const pathname = decodeURIComponent(new URL(req.url ?? '/', 'http://127.0.0.1').pathname);
    const page = pages.get(pathname);
    if (page !== undefined) {
      res.writeHead(200, { 'content-type': MIME['.html'] });
      res.end(page);
      return;
    }
    const file = path.resolve(root, `.${pathname.endsWith('/') ? `${pathname}index.html` : pathname}`);
    if (file !== root && !file.startsWith(root + path.sep)) {
      res.writeHead(403).end();
      return;
    }
    fs.readFile(file, (error, data) => {
      if (error) {
        res.writeHead(404).end();
        return;
      }
      res.writeHead(200, { 'content-type': MIME[path.extname(file)] ?? 'application/octet-stream' });
      res.end(data);
    });
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = /** @type {import('node:net').AddressInfo} */ (server.address());
      resolve({
        origin: `http://127.0.0.1:${address.port}`,
        close: () => new Promise((done) => server.close(() => done(undefined))),
      });
    });
  });
}

/**
 * Open one page that judges each navigation on its own: what it collects resets on every `goto`.
 * @param {import('playwright').Browser} browser
 * @param {string} origin
 */
async function openJudge(browser, origin) {
  const context = await browser.newContext();
  await context.addInitScript(INIT_SCRIPT);
  const page = await context.newPage();
  /** @type {string[]} */
  let problems = [];
  page.on('pageerror', (error) => problems.push(`uncaught exception: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') problems.push(`console.error: ${message.text()}`);
  });
  page.on('requestfailed', (request) => {
    // A request the next navigation cancels is not a file that failed to load.
    if (!request.url().startsWith(origin) || request.failure()?.errorText === 'net::ERR_ABORTED') return;
    problems.push(`request failed: ${request.url()} (${request.failure()?.errorText})`);
  });
  page.on('response', (response) => {
    if (response.url().startsWith(origin) && response.status() >= 400) {
      problems.push(`request failed: ${response.url()} (HTTP ${response.status()})`);
    }
  });

  return {
    /**
     * Navigate to `url` and return every reason it fails, empty when it renders.
     * @param {string} url
     * @param {number} boundMs
     * @returns {Promise<string[]>}
     */
    async judge(url, boundMs) {
      problems = [];
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      /** @type {{ event: keyof typeof VERDICT_EVENTS, detail: string } | null} */
      let verdict = null;
      try {
        const handle = await page.waitForFunction(() => /** @type {any} */ (globalThis).__bzVerdict, null, {
          timeout: boundMs,
        });
        verdict = await handle.jsonValue();
      } catch {
        problems.push(`no verdict inside ${boundMs / 1000}s`);
      }
      await page.waitForTimeout(SETTLE_MS);
      if (verdict && !VERDICT_EVENTS[verdict.event]) {
        problems.unshift(`${verdict.event}${verdict.detail ? `: ${verdict.detail}` : ''}`);
      }
      return [...problems];
    },
    close: () => context.close(),
  };
}

/**
 * Refuse to run when the judge misjudges a planted page.
 * @param {ReturnType<typeof openJudge> extends Promise<infer J> ? J : never} judge
 * @param {string} origin
 */
async function assertJudgeWorks(judge, origin) {
  /** @type {string[]} */
  const misjudged = [];
  for (const planted of PLANTED_PAGES) {
    const problems = await judge.judge(`${origin}${SELF_TEST_PREFIX}${planted.name}.html`, SELF_TEST_BOUND_MS);
    if ((problems.length === 0) !== planted.passes) {
      misjudged.push(
        `  ${planted.name}: expected ${planted.passes ? 'a render' : 'a failure'}, judged ${
          problems.length === 0 ? 'a render' : `a failure (${clip(problems.join('; '))})`
        }`,
      );
    }
  }
  if (misjudged.length > 0) {
    throw new Error(`story-render: the self-test misjudged planted pages, so no story was opened:\n${misjudged.join('\n')}`);
  }
}

/**
 * The index's stories: entries of type `story` and subtype `story`.
 * @param {string} dir
 * @returns {{ id: string, title: string }[]}
 */
function readStories(dir) {
  const indexFile = path.join(dir, 'index.json');
  if (!fs.existsSync(indexFile)) {
    throw new Error(`story-render: ${indexFile} does not exist — build the workshop first (npm run storybook:build)`);
  }
  const index = JSON.parse(fs.readFileSync(indexFile, 'utf8'));
  return Object.values(index.entries ?? {})
    .filter((entry) => entry.type === 'story' && entry.subtype === 'story')
    .map((entry) => ({ id: entry.id, title: `${entry.title} / ${entry.name}` }));
}

async function main() {
  const dir = path.resolve(process.argv[2] ?? DEFAULT_BUILD_DIR);
  const stories = readStories(dir);
  if (stories.length === 0) {
    throw new Error(`story-render: ${path.join(dir, 'index.json')} holds no story — an empty workshop cannot pass`);
  }

  const pages = new Map(PLANTED_PAGES.map((p) => [`${SELF_TEST_PREFIX}${p.name}.html`, plantedPage(p.body)]));
  const server = await serve(dir, pages);
  const browser = await chromium.launch({ headless: true });
  try {
    const judge = await openJudge(browser, server.origin);
    await assertJudgeWorks(judge, server.origin);

    let rendered = 0;
    for (const story of stories) {
      const problems = await judge.judge(
        `${server.origin}/iframe.html?id=${encodeURIComponent(story.id)}&viewMode=story`,
        VERDICT_BOUND_MS,
      );
      if (problems.length === 0) {
        rendered++;
        continue;
      }
      process.exitCode = 1;
      console.error(`story-render: ${story.id} (${story.title}) failed:`);
      for (const problem of problems) console.error(`  ${clip(problem)}`);
    }
    console.log(`story-render: ${rendered} of ${stories.length} stories rendered.`);
  } finally {
    await browser.close();
    await server.close();
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
