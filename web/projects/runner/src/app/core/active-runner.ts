import { InjectionToken, type Provider, type Signal, type WritableSignal, computed, inject, signal } from '@angular/core';
import { type DaemonTarget, runnerClient } from 'fleet';

/**
 * The runner the panel is on — the id a `/runners/:runnerId/…` route names, or `null` on
 * the host's own pages. One root-provided signal: only the shell writes it, from the
 * router; every page, query, nav control, and the chunk page's daemon reads it through
 * {@link injectActiveRunner} and never reads the route themselves.
 */
export const ACTIVE_RUNNER = new InjectionToken<WritableSignal<string | null>>('runner.ACTIVE_RUNNER', {
  providedIn: 'root',
  factory: () => signal<string | null>(null),
});

/** The runner the panel is on now, `null` on a host page — {@link ACTIVE_RUNNER}'s value, read-only. */
export function injectActiveRunner(): Signal<string | null> {
  return inject(ACTIVE_RUNNER).asReadonly();
}

/** The active runner as a target for `fleet`'s target-generic reads — the runner client,
 * addressed under the active runner's id — or `null` on a host page, holding every read. */
export function injectActiveRunnerTarget(): Signal<DaemonTarget | null> {
  const runnerId = injectActiveRunner();
  return computed(() => {
    const id = runnerId();
    return id === null ? null : { plane: 'runner', client: runnerClient, runnerId: id };
  });
}

/** Provides {@link ACTIVE_RUNNER} pinned to `runnerId` — for a surface mounted outside the
 * shell, which is otherwise the signal's only writer. */
export function provideActiveRunner(runnerId: string | null): Provider {
  return { provide: ACTIVE_RUNNER, useFactory: () => signal<string | null>(runnerId) };
}
