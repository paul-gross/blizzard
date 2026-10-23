import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { KitAvatar, KitMenu, KitMenuItem, KitMenuPanel, ViewportMenu } from 'fleet';
import { injectRunnerSessionQuery, signedInUsername } from '../../core/identity/auth.query';
import { RunnerLogout } from '../../core/identity/runner-logout';

/**
 * The runner desktop header's profile menu — the avatar-circle trigger holding `Log out`
 * and the appearance switcher. `AppHeader` projects it into the shared `BoardHeader`'s
 * `[header-trailing]` slot inside a `@defer` block: the CDK menu stack behind it is
 * interaction-only chrome, so it stays out of the initial chunk
 * (`bzh:frontend-eager-shell-entry`) — the runner's counterpart of the hub's `AppNavMenu`.
 *
 * Built on the kit's CDK-menu primitives, with the panel and its items declared here
 * rather than projected into {@link KitMenu}, because `CdkMenu` finds its items by a
 * content query that does not cross an `<ng-content>` boundary.
 *
 * `Log out` is gated on the shell's own session read and bound to {@link RunnerLogout},
 * the one logout owner; a failed logout is rendered by the header, outside the panel.
 */
@Component({
  selector: 'app-profile-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkMenuTrigger, KitAvatar, KitMenu, KitMenuItem, KitMenuPanel, ViewportMenu],
  templateUrl: './app-profile-menu.html',
  styleUrl: './app-profile-menu.css',
})
export class AppProfileMenu {
  protected readonly logout = inject(RunnerLogout);

  private readonly sessionQuery = injectRunnerSessionQuery();

  /** Whether a hub username is signed in — the gate on the `Log out` item. */
  protected readonly signedIn = computed(() => signedInUsername(this.sessionQuery.data()) !== null);
}
