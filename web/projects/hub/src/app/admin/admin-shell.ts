import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { KitTab, KitTabStrip } from 'fleet';

/**
 * The `/admin` route — its own second strip over the hub's administration surfaces,
 * each a child route (`app.routes.ts`) so a tab is a deep link: Users (the user
 * listing, unchanged), then one surface per configured noun — work sources,
 * repositories, secrets — and the change log across all of them. Users stays in the
 * strip at every width.
 */
@Component({
  selector: 'app-admin-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip, RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './admin-shell.html',
  styleUrl: './admin-shell.css',
})
export class AdminShell {}
