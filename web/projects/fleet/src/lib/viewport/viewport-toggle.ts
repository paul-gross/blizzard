import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { KitChips, type KitChipOption } from '../kit/kit-chips';
import { ViewportService, type ViewportOverride } from './viewport-service';

const OPTIONS: readonly KitChipOption[] = [
  { value: 'auto', label: 'Auto', testid: 'viewport-toggle-auto' },
  { value: 'mobile', label: 'Mobile', testid: 'viewport-toggle-mobile' },
  { value: 'desktop', label: 'Desktop', testid: 'viewport-toggle-desktop' },
];

/**
 * The viewport override control — lets a user pin `ViewportService`'s shell
 * choice to mobile or desktop, or leave it on `'auto'` (the breakpoint-derived
 * mode). Built from {@link KitChips} rather than a hand-rolled selector
 * (`bzh:frontend-kit`); the trailing span reads the *effective* mode
 * (`ViewportService.mode`), which differs from the selected chip whenever the
 * override is `'auto'`.
 */
@Component({
  selector: 'fleet-viewport-toggle',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitChips],
  template: `
    <div class="viewport-toggle">
      <fleet-kit-chips [options]="options" [selectedValue]="viewport.override()" (choose)="onChoose($event)" />
      <span class="mode-value" data-testid="viewport-toggle-mode">{{ viewport.mode() }}</span>
    </div>
  `,
  styles: `
    :host {
      display: contents;
    }
    .viewport-toggle {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .mode-value {
      font-size: var(--fs-xs);
      color: var(--label);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
  `,
})
export class ViewportToggle {
  protected readonly viewport = inject(ViewportService);
  protected readonly options = OPTIONS;

  protected onChoose(value: string): void {
    this.viewport.setOverride(value as ViewportOverride);
  }
}
