// @ts-check
const { defineConfig } = require("eslint/config");
const rootConfig = require("../../eslint.config.js");

module.exports = defineConfig([
  // The generated openapi-ts client is committed and drift-checked, not
  // hand-edited — never lint it (bzh:generated-client).
  { ignores: ["**/lib/api/**"] },
  ...rootConfig,
  {
    files: ["**/*.ts"],
    rules: {
      "@angular-eslint/directive-selector": [
        "error",
        {
          type: "attribute",
          prefix: "fleet",
          style: "camelCase",
        },
      ],
      "@angular-eslint/component-selector": [
        "error",
        [
          {
            type: "element",
            prefix: "fleet",
            style: "kebab-case",
          },
          // Attribute-selector components mirror the directive-selector convention
          // above — needed for an SVG child (`graph-diagram-node-shape.ts`) that must
          // render as a plain `<g>` inside a parent's `<svg>`, not a wrapping element.
          {
            type: "attribute",
            prefix: "fleet",
            style: "camelCase",
          },
        ],
      ],
    },
  },
  {
    files: ["**/*.html"],
    rules: {},
  },
]);
