import { Injectable, computed } from '@angular/core';
import { errorMessage } from 'fleet';

import { injectRunnerLogoutMutation } from './auth.query';

/**
 * The runner shell's one logout owner — the mutation, its in-flight flag, and its failure.
 * Root-provided rather than held by {@link LocalIdentity}: on mobile that component is
 * constructed inside the menu overlay and destroyed when the menu closes, so it could not
 * hold an error that must outlive the click. Every Log out trigger binds its `[disabled]`
 * to {@link pending}; the error renders outside any menu panel.
 */
@Injectable({ providedIn: 'root' })
export class RunnerLogout {
  private readonly mutation = injectRunnerLogoutMutation();

  /** Whether the logout is in flight. */
  readonly pending = computed(() => this.mutation.isPending());

  /** The failed logout's message, `null` while there is none. */
  readonly error = computed(() => (this.mutation.isError() ? errorMessage(this.mutation.error(), 'Log out failed') : null));

  /** Clears the session and reloads on success; a failure surfaces through {@link error}, never as a rejection. */
  async logout(): Promise<void> {
    try {
      await this.mutation.mutateAsync();
    } catch {
      return;
    }
    this.reload();
  }

  /** Full page load so the served shell's SSO gate re-evaluates the next visit — factored out so it can be stubbed. */
  protected reload(): void {
    globalThis.location.assign('/');
  }
}
