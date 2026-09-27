/** Ambient module declaration for the SSE golden-contract JSON imports —
 * lets `sse-contract.spec.ts` import `contracts/sse/*.json` directly without turning on
 * `resolveJsonModule` project-wide, so the change stays inside `lib/sse/`. */
declare module '*.json' {
  const value: unknown;
  export default value;
}
