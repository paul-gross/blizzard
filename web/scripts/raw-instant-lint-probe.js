// @ts-check
const assert = require('node:assert/strict');
const { ESLint } = require('eslint');

/** Proves the template lint refuses a wire instant rendered raw and passes the sanctioned forms. */
async function main() {
  const eslint = new ESLint();
  const appPath = 'projects/hub/src/app/board/board-shell/board-column.html';
  const refused = async (html) => {
    const [result] = await eslint.lintText(html, { filePath: appPath });
    return result.messages.some((message) => message.ruleId === 'no-restricted-syntax');
  };
  for (const html of [
    '<p>{{ change.recorded_at }}</p>',
    '<span [title]="ev.recorded_at">x</span>',
  ]) assert(await refused(html), `${html} must be refused`);
  for (const html of [
    '<p>{{ age(ev.recorded_at) }}</p>',
    '<span [title]="absolute(ev.recorded_at)">x</span>',
    '<p>{{ fact.acked_at !== null ? "yes" : "no" }}</p>',
    '<fleet-when [iso]="ev.recorded_at" />',
    '<!-- eslint-disable-next-line no-restricted-syntax -- an instant shown verbatim -->\n<p>{{ ev.recorded_at }}</p>',
  ]) assert(!(await refused(html)), `${html} must pass`);
  console.log('raw-instant-lint-probe: all AST cases clean');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
