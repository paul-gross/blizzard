import { inject } from '@angular/core';
import { type CanActivateFn, RedirectCommand, Router } from '@angular/router';

import { AppScope } from '../app-scope';
import { FlagStore } from './flag-store';

/**
 * Keeps a route unreachable by URL while flag `name` is off: waits for the flags read to settle, then lets the
 * navigation through when the flag is on, else redirects to the app's root — through {@link AppScope.path}, so the hub
 * keeps the project lens the board is on. A failed read counts as off.
 */
export function flagGuard(name: string): CanActivateFn {
  return async () => {
    const flags = inject(FlagStore);
    const router = inject(Router);
    const link = inject(AppScope).path('/');
    await flags.whenSettled();
    if (flags.on(name)) return true;
    const root = typeof link === 'string' ? router.parseUrl(link) : router.createUrlTree([...link]);
    return new RedirectCommand(root, { replaceUrl: true });
  };
}
