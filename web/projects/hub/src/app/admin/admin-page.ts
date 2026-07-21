import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The `/admin` route — a deliberate stub (issue #93's nav-gating scope note: "a stub
 * route is fine"). The admin page itself — the user list and role-assignment table —
 * is issue #94's own slice; this phase only lands the `Admin` nav entry and its
 * `user:manage` gate (`app-nav.ts`), so the route it points at must already exist.
 */
@Component({
  selector: 'app-admin-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stub" data-testid="admin-page-stub">
      <p>User administration is coming soon.</p>
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
    .stub {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      color: var(--label);
      font-family: var(--mono);
      letter-spacing: 0.08em;
    }
  `,
})
export class AdminPage {}
