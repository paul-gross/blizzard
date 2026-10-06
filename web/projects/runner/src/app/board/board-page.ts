import { ChangeDetectionStrategy, Component } from '@angular/core';
import { LocalPanel } from './app-panel';

/**
 * The `/board` route — the machine-local panel. A thin route wrapper
 * around {@link LocalPanel}, mirroring the hub's own page components: the
 * route owns nothing beyond mounting it.
 */
@Component({
  selector: 'app-board-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalPanel],
  templateUrl: './board-page.html',
  styleUrl: './board-page.css',
})
export class BoardPage {}
