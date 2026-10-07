// @ts-check
const eslint = require("@eslint/js");
const { defineConfig } = require("eslint/config");
const tseslint = require("typescript-eslint");
const angular = require("angular-eslint");

// No frontend code reads the wall clock implicitly (`bzh:frontend-formatters`): a time
// helper takes `now` from `injectNowSignal` (a display that must advance) or
// `FLEET_CLOCK` (a single reading). Both live in `fleet/lib/core/now-signal/`, the one
// place exempt from this ban.
const CLOCK_MESSAGE =
  "Read the clock through `injectNowSignal` (a ticking display) or `FLEET_CLOCK` (a single reading), never a raw `Date.now()` / zero-arg `new Date()`.";
const CLOCK_SELECTORS = [
  { selector: "NewExpression[callee.name='Date'][arguments.length=0]", message: CLOCK_MESSAGE },
  { selector: "CallExpression[callee.object.name='Date'][callee.property.name='now']", message: CLOCK_MESSAGE },
];

module.exports = defineConfig([
  {
    files: ["**/*.ts"],
    extends: [
      eslint.configs.recommended,
      tseslint.configs.recommended,
      tseslint.configs.stylistic,
      angular.configs.tsRecommended,
    ],
    processor: angular.processInlineTemplates,
    rules: {
      "@angular-eslint/directive-selector": [
        "error",
        {
          type: "attribute",
          prefix: "app",
          style: "camelCase",
        },
      ],
      "@angular-eslint/component-selector": [
        "error",
        {
          type: "element",
          prefix: "app",
          style: "kebab-case",
        },
      ],
      // Application components must keep their template and styles in sibling
      // .html/.css files, not inline in the decorator. 0 means no inline
      // declaration of any length is allowed.
      "@angular-eslint/component-max-inline-declarations": [
        "error",
        { template: 0, styles: 0, animations: 0 },
      ],
      // The ~400-line cap, moved here from `web/scripts/structural-gate.js`'s own walk.
      // No architecture doc declares the number itself — in practice the files it has
      // caught were also blizzard-context bzh:frontend-container-presentational splits
      // (an oversized component is often evidence of merged container/presentational
      // concerns), but the cap now reaches every `.ts` file this config sees, not only
      // components. Not a byte-for-byte port either: eslint's own `max-lines` counts
      // real lines the way `wc -l` does, one lower than structural-gate's
      // `source.split('\n').length` for any file ending in the usual trailing newline
      // — a file at exactly 400 real lines that used to fail now passes.
      "max-lines": ["error", { max: 400, skipBlankLines: false, skipComments: false }],
    },
  },
  // Spec files declare inline @Component test-host fixtures — test scaffolding,
  // not application components — so they're exempt from the inline-declarations ban.
  // Specs also run well past 400 lines by nature (one `it` per case); 800 is a
  // runaway guard against a spec growing unbounded, not design pressure.
  {
    files: ["**/*.spec.ts"],
    rules: {
      "@angular-eslint/component-max-inline-declarations": "off",
      "max-lines": ["error", { max: 800, skipBlankLines: false, skipComments: false }],
    },
  },
  {
    files: ["projects/**/*.ts"],
    ignores: ["**/*.spec.ts", "projects/fleet/src/lib/core/now-signal/**"],
    rules: {
      "no-restricted-syntax": ["error", ...CLOCK_SELECTORS],
    },
  },
  {
    files: ["**/*.html"],
    extends: [
      angular.configs.templateRecommended,
      angular.configs.templateAccessibility,
    ],
    rules: {},
  },
  // Kit owns native control chrome. A distinct native interaction needs a
  // reasoned, single-element template suppression rather than a new CSS name.
  {
    // Each Angular project loads this shared config from its own directory.
    files: ["**/src/**/*.html"],
    ignores: ["**/src/lib/kit/**/*.html", "src/lib/kit/**/*.html"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "Element[name=/^(button|select|input)$/]",
          message: "Use the fleet kit control; explain a genuinely distinct native interaction in an element-level ESLint suppression.",
        },
      ],
    },
  },
  // A sub-barrel names what's re-stackable outside its feature directory
  // (`bzh:frontend-disjoint-diffs`); a blanket `export *` makes that decision unmakeable,
  // since every symbol added under the feature directory becomes public with no diff on
  // the barrel. Scoped to `projects/*/src/lib/**/index.ts` so fleet's own top-level
  // `public-api.ts` — which legitimately stars its sub-barrels — stays legal.
  // Flat config replaces a rule's options per matching block, so this block restates
  // the clock ban alongside its own selector.
  {
    files: ["projects/*/src/lib/**/index.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        ...CLOCK_SELECTORS,
        {
          selector: "ExportAllDeclaration",
          message: "A sub-barrel exports only what a consumer outside the feature directory imports — name it.",
        },
      ],
    },
  },
  // The workspace's own CommonJS config files (the dev-server proxies, this file).
  // Without this block they are the only source in the repo no linter reads.
  {
    files: ["*.js"],
    extends: [eslint.configs.recommended],
    languageOptions: {
      sourceType: "commonjs",
      globals: { module: "writable", require: "readonly", process: "readonly" },
    },
  },
]);
