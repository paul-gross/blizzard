import { BreakpointObserver } from '@angular/cdk/layout';
import { Injectable, type Signal, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { map } from 'rxjs';

/** The manual viewport override a user can pin from {@link ViewportToggle} —
 * `'auto'` defers to the breakpoint-derived {@link ViewportMode}. */
export type ViewportOverride = 'auto' | 'mobile' | 'desktop';

/** The effective shell a page renders — see `../docs/designs/mobile/README.md`'s
 * "adaptive shells over shared guts": a page picks between a mobile and a
 * desktop shell at runtime, both built from the same leaf components. */
export type ViewportMode = 'mobile' | 'desktop';

/** localStorage key the override is persisted under, read back on next load. */
const STORAGE_KEY = 'blizzard.viewport.override';

/** The mobile/desktop split — a 767.98px cutoff (Bootstrap's own `md` break),
 * chosen so a real handset and a small tablet both read as mobile. */
const MOBILE_QUERY = '(max-width: 767.98px)';

/** Read the persisted override, tolerating a missing/corrupt/inaccessible
 * localStorage (private browsing, quota, disabled storage) by falling back to
 * `'auto'` rather than throwing during construction. */
function readStoredOverride(): ViewportOverride {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === 'mobile' || stored === 'desktop' || stored === 'auto' ? stored : 'auto';
  } catch {
    return 'auto';
  }
}

/**
 * The viewport service — the one seam a page consults to pick its shell
 * (mobile vs. desktop). Two signals drive `mode`: the CDK's
 * `BreakpointObserver` for the breakpoint-derived mode, and a manual
 * `override` (persisted to localStorage) a user can pin instead of the
 * breakpoint — {@link ViewportToggle} is the control that sets it.
 */
@Injectable({ providedIn: 'root' })
export class ViewportService {
  private readonly breakpointObserver = inject(BreakpointObserver);

  /** The breakpoint-derived mode, converted from the CDK's observable to a
   * signal — `initialValue` is the synchronous match so the first render
   * (before the observable's first emission lands) is already correct. */
  private readonly breakpointMode: Signal<ViewportMode> = toSignal(
    this.breakpointObserver.observe(MOBILE_QUERY).pipe(map((state) => (state.matches ? 'mobile' : 'desktop'))),
    { initialValue: this.breakpointObserver.isMatched(MOBILE_QUERY) ? 'mobile' : 'desktop' },
  );

  private readonly overrideSignal = signal<ViewportOverride>(readStoredOverride());

  /** The manual override, initialized from localStorage. */
  readonly override: Signal<ViewportOverride> = this.overrideSignal.asReadonly();

  /** The effective mode: `override` when it isn't `'auto'`, otherwise the
   * breakpoint-derived mode. */
  readonly mode: Signal<ViewportMode> = computed(() => {
    const override = this.overrideSignal();
    return override === 'auto' ? this.breakpointMode() : override;
  });

  /** Set the override and persist it — a storage failure degrades to
   * in-memory only, the signal still updates for the current session. */
  setOverride(value: ViewportOverride): void {
    this.overrideSignal.set(value);
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch {
      // Private browsing / quota / disabled storage — in-memory only for this session.
    }
  }
}
