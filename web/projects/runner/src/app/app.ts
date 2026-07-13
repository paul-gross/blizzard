import { ChangeDetectionStrategy, Component } from '@angular/core';
import { LocalPanel } from 'local-panel';

/**
 * The runner local-panel app — a thin entrypoint that renders the machine-local
 * panel shell. It composes the shared fleet library (design tokens, and the
 * fleet views as they arrive) plus the runner-only local-panel library.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalPanel],
  template: `<fleet-local-panel />`,
  styles: `:host { display: block; height: 100%; }`,
})
export class App {}
