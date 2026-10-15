// @ts-check
const hubConfig = require("./projects/hub/eslint.config.js");

// The hub's own lint config, re-rooted onto the web exemplars (`exemplars/web/`): that tree mirrors `projects/`, so each
// `projects/`-rooted pattern drops that prefix. Run from the exemplar tree, which `--config` makes the base path. The
// generated-client stand-in is skipped as `fleet`'s own config skips the generated client.

/** @param {unknown} pattern */
const reroot = (pattern) => (typeof pattern === "string" ? pattern.replace(/^projects\//, "") : pattern);

module.exports = [
  { ignores: ["**/lib/api/**"] },
  ...hubConfig.map((block) => ({
    ...block,
    ...(block.files ? { files: block.files.map(reroot) } : {}),
    ...(block.ignores ? { ignores: block.ignores.map(reroot) } : {}),
  })),
];
