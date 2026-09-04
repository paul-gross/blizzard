/** `@angular/build:unit-test`'s vitest executor disposes the `Vitest` instance without
 * the vitest CLI's own `ctx.exit()` safety net (angular/angular-cli#32832): if a pool
 * worker or the esbuild transform service it spawns doesn't respond to shutdown, the
 * process's event loop stays alive on the orphaned handle and `ng test` hangs forever
 * past a clean "all tests passed" summary. This mirrors `ctx.exit()`'s own safety net —
 * an unref'd timer that forces the process down if disposal is still stuck. It never
 * fires on a normal run: `_teardownGlobalSetup()` runs during `Vitest.close()`, well
 * before a hung `pool.close()` would return.
 */
export async function teardown(): Promise<void> {
  setTimeout(() => process.exit(process.exitCode ?? 0), 10_000).unref();
}
