import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitMenuItem, KitMenuPanel } from '../kit';
import { MobileTitlebar } from './mobile-titlebar';

/** The titlebar takes its menu as a template, so every spec mounts it through a
 * host that declares one — the same contract the two mobile shells honor. */
@Component({
  selector: 'fleet-test-host',
  imports: [KitMenuItem, KitMenuPanel, MobileTitlebar],
  template: `
    <fleet-mobile-titlebar [live]="live()" [testid]="testid()" [menu]="shellMenu" />
    <ng-template #shellMenu>
      <fleet-kit-menu-panel [testid]="testid() + '-menu-panel'">
        <fleet-kit-menu-item testid="shell-item">Appearance</fleet-kit-menu-item>
      </fleet-kit-menu-panel>
    </ng-template>
  `,
})
class TestHost {
  readonly live = signal(true);
  readonly testid = signal('mobile-titlebar');
}

describe('MobileTitlebar', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<TestHost>>;
  let el: HTMLElement;

  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
  });

  it('renders the brand mark and wordmark under its default testid', () => {
    expect(el.querySelector('[data-testid="mobile-titlebar"]')?.textContent).toContain('blizzard');
    expect(el.querySelector('fleet-brand-mark')).not.toBeNull();
  });

  it('reflects the live input on the live dot', async () => {
    fixture.componentInstance.live.set(false);
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="mobile-titlebar-livedot"]')?.classList.contains('active')).toBe(false);

    fixture.componentInstance.live.set(true);
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="mobile-titlebar-livedot"]')?.classList.contains('active')).toBe(true);
  });

  it('buries the consumer-supplied menu panel, closed by default', async () => {
    expect(document.body.querySelector('[data-testid="mobile-titlebar-menu-panel"]')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="mobile-titlebar-menu"]')?.click();
    await fixture.whenStable();

    // The CDK renders the panel into an overlay on `document.body`, not inside
    // the titlebar's own element.
    expect(document.body.querySelector('[data-testid="mobile-titlebar-menu-panel"] [data-testid="shell-item"]')).not.toBeNull();
  });

  it('derives every handle it renders from a custom testid, so two mounts never collide', async () => {
    fixture.componentInstance.testid.set('runner-mobile-titlebar');
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="runner-mobile-titlebar"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="runner-mobile-titlebar-livedot"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="runner-mobile-titlebar-menu"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="mobile-titlebar"]')).toBeNull();
  });
});
