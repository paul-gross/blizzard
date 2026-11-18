import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitTabContent } from './kit-tab-content';
import { KitTabGroup } from './kit-tab-group';
import { KitTabPanel } from './kit-tab-panel';
import { KitTabs, type KitTabOption } from './kit-tabs';

const OPTIONS: KitTabOption[] = [
  { value: 'general', label: 'General', testid: 'tab-general' },
  { value: 'artifacts', label: 'Artifacts', testid: 'tab-artifacts' },
];

@Component({
  selector: 'fleet-test-host',
  imports: [KitTabs, KitTabGroup, KitTabPanel, KitTabContent],
  template: `
    <section fleetKitTabGroup>
      <fleet-kit-tabs [options]="options()" [(active)]="active" (activeChange)="changes.push($event)" />
      @for (option of options(); track option.value) {
        <div [fleetKitTabPanel]="option.value" [attr.data-panel]="option.value">
          <ng-template fleetKitTabContent><p class="body">{{ option.label }} body</p></ng-template>
        </div>
      }
    </section>
  `,
})
class TestHost {
  readonly options = signal<KitTabOption[]>(OPTIONS);
  readonly active = signal<string | null>(null);
  readonly changes: (string | null)[] = [];
}

function key(el: Element, name: string): void {
  el.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
}

describe('KitTabs', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders one tab per option', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const tabs = el.querySelectorAll('.tab');
    expect(tabs).toHaveLength(2);
    expect(tabs[0].textContent?.trim()).toBe('General');
    expect(tabs[1].textContent?.trim()).toBe('Artifacts');
  });

  it('shows a count after a label that carries one, and none otherwise', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.options.set([
      { value: 'links', label: 'Linked sources', count: 2 },
      { value: 'none', label: 'Linked repositories', count: 0 },
      { value: 'overview', label: 'Overview' },
    ]);
    await fixture.whenStable();
    const tabs = (fixture.nativeElement as HTMLElement).querySelectorAll('.tab');
    expect(tabs[0].querySelector('.cnt')?.textContent).toBe('2');
    expect(tabs[1].querySelector('.cnt')?.textContent).toBe('0');
    expect(tabs[2].querySelector('.cnt')).toBeNull();
  });

  it('marks the active option and emits activeChange with the clicked value', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set('general');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const tabs = el.querySelectorAll('.tab');
    expect(tabs[0].classList.contains('active')).toBe(true);
    expect(tabs[1].classList.contains('active')).toBe(false);
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(tabs[1].getAttribute('aria-selected')).toBe('false');
    expect(fixture.componentInstance.changes).toEqual([]);

    (tabs[1] as HTMLButtonElement).click();
    await fixture.whenStable();
    expect(fixture.componentInstance.changes).toEqual(['artifacts']);
    expect(fixture.componentInstance.active()).toBe('artifacts');
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
  });

  it('shows only the active tab panel body', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set('general');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(Array.from(el.querySelectorAll('.body')).map((b) => b.textContent)).toEqual(['General body']);
    expect(el.querySelector<HTMLElement>('[data-panel="artifacts"]')!.style.display).toBe('none');
    expect(el.querySelector<HTMLElement>('[data-panel="general"]')!.style.display).toBe('');
    expect(el.querySelector('[data-panel="general"]')!.getAttribute('role')).toBe('tabpanel');

    fixture.componentInstance.active.set('artifacts');
    await fixture.whenStable();
    expect(Array.from(el.querySelectorAll('.body')).map((b) => b.textContent)).toEqual(['Artifacts body']);
  });

  it('follows a programmatic change of the active value without emitting', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set('general');
    await fixture.whenStable();
    fixture.componentInstance.active.set('artifacts');
    await fixture.whenStable();
    const tabs = (fixture.nativeElement as HTMLElement).querySelectorAll('.tab');
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
    expect(tabs[0].getAttribute('aria-selected')).toBe('false');
    expect(fixture.componentInstance.changes).toEqual([]);
  });

  it('selects a bound value whose tab arrives later, without emitting', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set('transcripts');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(fixture.componentInstance.active()).toBe('transcripts');

    fixture.componentInstance.options.set([...OPTIONS, { value: 'transcripts', label: 'Transcripts' }]);
    await fixture.whenStable();
    const tabs = el.querySelectorAll('.tab');
    expect(tabs[2].getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('.body')?.textContent).toBe('Transcripts body');
    expect(fixture.componentInstance.changes).toEqual([]);
  });

  it('moves selection with the arrow keys, Home and End as a person would', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.options.set([...OPTIONS, { value: 'node', label: 'Node' }]);
    fixture.componentInstance.active.set('general');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const tabs = el.querySelectorAll<HTMLElement>('.tab');
    expect(Array.from(tabs).map((t) => t.tabIndex)).toEqual([0, -1, -1]);

    tabs[0].focus();
    key(tabs[0], 'ArrowRight');
    await fixture.whenStable();
    expect(fixture.componentInstance.active()).toBe('artifacts');
    key(tabs[1], 'End');
    await fixture.whenStable();
    expect(fixture.componentInstance.active()).toBe('node');
    key(tabs[2], 'Home');
    await fixture.whenStable();
    expect(fixture.componentInstance.active()).toBe('general');
    expect(fixture.componentInstance.changes).toEqual(['artifacts', 'node', 'general']);
  });

  it('marks the strip and each tab with their ARIA roles', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[role="tablist"]')).not.toBeNull();
    expect(el.querySelectorAll('[role="tab"]')).toHaveLength(2);
  });

  it('forwards each option testid to its tab', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-general"]')?.textContent?.trim()).toBe('General');
    expect(el.querySelector('[data-testid="tab-artifacts"]')?.textContent?.trim()).toBe('Artifacts');
  });

  it('shares the nav tab strip chrome (blizzard#203)', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[role="tablist"][fleetKitTabStrip]')).not.toBeNull();
    expect(el.querySelectorAll('.tab[fleetKitTab]')).toHaveLength(2);
  });
});
