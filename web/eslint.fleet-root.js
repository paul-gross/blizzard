// @ts-check
const { builtinRules } = require("eslint/use-at-your-own-risk");

// Nothing imports the `fleet` root (`bzh:frontend-weight-class-entries`): the library is consumed through its
// `fleet/<entry>` paths, each reaching only what its members need. The selectors refuse every way of naming it —
// an import, a re-export, a dynamic `import()`, and an `import('fleet')` type.
//
// The ban is the built-in `no-restricted-syntax` registered under its own name: flat config replaces a rule's
// options per matching block, so a shared rule name would overwrite the option sets the root config already gives
// these files. `web/scripts/fleet-root-lint-probe.js` proves it.

const MESSAGE = "Import a `fleet/<entry>` path (see `web/tsconfig.json`), never the `fleet` root.";

const SELECTORS = [
  "ImportDeclaration[source.value='fleet']",
  "ExportNamedDeclaration[source.value='fleet']",
  "ExportAllDeclaration[source.value='fleet']",
  "ImportExpression[source.value='fleet']",
  "TSImportType[argument.literal.value='fleet']",
  "TSImportType[source.value='fleet']",
];

module.exports = [
  {
    files: ["**/*.ts"],
    plugins: { "fleet-root": { rules: { "no-root-import": builtinRules.get("no-restricted-syntax") } } },
    rules: { "fleet-root/no-root-import": ["error", ...SELECTORS.map((selector) => ({ selector, message: MESSAGE }))] },
  },
];
