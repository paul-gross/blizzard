// @ts-check
const eslint = require("@eslint/js");
const { defineConfig } = require("eslint/config");
const tseslint = require("typescript-eslint");
const angular = require("angular-eslint");

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
    },
  },
  // Spec files declare inline @Component test-host fixtures — test scaffolding,
  // not application components — so they're exempt from the inline-declarations ban.
  {
    files: ["**/*.spec.ts"],
    rules: {
      "@angular-eslint/component-max-inline-declarations": "off",
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
