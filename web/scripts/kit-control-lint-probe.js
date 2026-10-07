// @ts-check
const assert = require('node:assert/strict');
const { ESLint } = require('eslint');

async function main() {
  const eslint = new ESLint();
  const appPath = 'projects/hub/src/app/board/board-card/board-card.html';
  const kitPath = 'projects/fleet/src/lib/kit/kit-button.html';
  for (const tag of ['button', 'select', 'input']) {
    const html = tag === 'input' ? '<input />' : `<${tag}></${tag}>`;
    const [result] = await eslint.lintText(html, { filePath: appPath });
    assert(result.messages.some((message) => message.ruleId === 'no-restricted-syntax'), `${tag} must be refused`);
    const [kit] = await eslint.lintText(html, { filePath: kitPath });
    assert(!kit.messages.some((message) => message.ruleId === 'no-restricted-syntax'), `kit ${tag} must pass`);
  }
  for (const html of [
    '<fleet-kit-button>Action</fleet-kit-button>',
    '<!-- <button>example</button> -->',
    '<!-- eslint-disable-next-line no-restricted-syntax -- native control with distinct semantics -->\n<button>Action</button>',
  ]) {
    const [result] = await eslint.lintText(html, { filePath: appPath });
    assert(!result.messages.some((message) => message.ruleId === 'no-restricted-syntax'), `${html} must pass`);
  }
  console.log('kit-control-lint-probe: all AST cases clean');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
