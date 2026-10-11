// @ts-check
const { builtinRules } = require("eslint/use-at-your-own-risk");

// Every in-app link in the hub app and the fleet library keeps the project lens: a
// template link goes through the `lensLink` pipe, and a navigation to an absolute path
// passes its commands through `ProjectLens.link`. `scripts/lens-links.mjs` performs that
// rewrite; this ban keeps a new literal absolute link from bypassing it. The public
// pages (`/login`, `/invite…`) and a path already under a lens (`/p/…`) are never
// lensed, so they are exempt. The runner app has no lens and does not load this config.
//
// The ban is `no-restricted-syntax` registered under its own name, so its selectors
// never replace the root config's `no-restricted-syntax` options for the same files.
//
// Files allowed to hold a literal absolute in-app link, relative to the project root.
/** @type {string[]} */
const ABSOLUTE_LINK_ALLOWED = [];

const MESSAGE =
  "An in-app link keeps the project lens: pipe a template link through `lensLink`, and pass a navigation's commands through `ProjectLens.link`.";

// esquery regexes cannot hold a `/`, so the leading slash is written `[\x2F]`.
const ABSOLUTE = "/^[\\x2F]/";
const LENSABLE = "/^[\\x2F](?!(login|invite|p)\\b)/";

const TEMPLATE_SELECTORS = [
  `TextAttribute[name='routerLink'][value=${LENSABLE}]`,
  "BoundAttribute[name='routerLink'][value.ast.name!='lensLink']",
];

const CLASS_SELECTORS = [
  `CallExpression[callee.property.name='navigate'] > ArrayExpression:first-child > Literal:first-child[value=${ABSOLUTE}]`,
  `CallExpression[callee.property.name='navigate'] > ArrayExpression:first-child > TemplateLiteral:first-child > TemplateElement:first-child[value.raw=${ABSOLUTE}]`,
  `CallExpression[callee.property.name='navigateByUrl'] > Literal:first-child[value=${LENSABLE}]`,
  `CallExpression[callee.property.name='navigateByUrl'] > TemplateLiteral:first-child > TemplateElement:first-child[value.raw=${LENSABLE}]`,
];

const lens = { rules: { "no-absolute-link": builtinRules.get("no-restricted-syntax") } };

/** @param {string[]} selectors */
const restrict = (selectors) => ["error", ...selectors.map((selector) => ({ selector, message: MESSAGE }))];

module.exports = [
  {
    files: ["**/*.ts"],
    ignores: ["**/*.spec.ts", ...ABSOLUTE_LINK_ALLOWED],
    plugins: { lens },
    rules: { "lens/no-absolute-link": restrict(CLASS_SELECTORS) },
  },
  {
    files: ["**/*.html"],
    ignores: ABSOLUTE_LINK_ALLOWED,
    plugins: { lens },
    rules: { "lens/no-absolute-link": restrict(TEMPLATE_SELECTORS) },
  },
];
