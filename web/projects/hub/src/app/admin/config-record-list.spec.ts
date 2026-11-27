import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ConfigRecordList, type ConfigRowVm } from './config-record-list';

const ROWS: readonly ConfigRowVm[] = [
  { key: 'hub', title: 'hub', sub: [], badges: [{ label: 'built-in', tone: 'spawning' }], revision: null, retired: false },
  { key: 'winter', title: 'winter', sub: ['github', 'paul-gross/winter'], badges: [], revision: 2, retired: false },
];

function key(el: Element, name: string): void {
  el.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
}

describe('ConfigRecordList', () => {
  async function mount(inputs: { rows?: readonly ConfigRowVm[]; state?: 'loading' | 'error' | 'empty' | 'ready' }) {
    await TestBed.configureTestingModule({
      imports: [ConfigRecordList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ConfigRecordList);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('emptyText', 'No active work sources.');
    fixture.componentRef.setInput('selectedKey', 'winter');
    fixture.componentRef.setInput('testidPrefix', 'ws');
    await fixture.whenStable();
    return fixture;
  }

  it('renders each row with its badges, revision, and facts, the selected one marked', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    const hub = el.querySelector('[data-testid="ws-row-hub"]')!;
    expect(hub.textContent).toContain('built-in');
    expect(hub.textContent).not.toMatch(/r\d/);
    expect(hub.classList.contains('selected')).toBe(false);
    const winter = el.querySelector('[data-testid="ws-row-winter"]')!;
    expect(winter.textContent).toContain('r2');
    expect(winter.textContent).toContain('paul-gross/winter');
    expect(winter.classList.contains('selected')).toBe(true);
  });

  it('opens a row on click and on Enter', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    const picked: string[] = [];
    // The route feeds each open back in as the selection.
    fixture.componentInstance.pick.subscribe((picked_) => {
      picked.push(picked_);
      fixture.componentRef.setInput('selectedKey', picked_);
    });
    const hub = el.querySelector<HTMLElement>('[data-testid="ws-row-hub"]')!;
    hub.click();
    await fixture.whenStable();
    key(hub, 'ArrowDown');
    await fixture.whenStable();
    key(hub, 'Enter');
    await fixture.whenStable();
    expect(picked).toEqual(['hub', 'winter']);
  });

  it('marks the row the route-backed key names as the selected option', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    expect(
      el
        .querySelector('[data-testid="ws-row-winter"]')!
        .getAttribute('aria-selected'),
    ).toBe('true');
    fixture.componentRef.setInput('selectedKey', 'hub');
    await fixture.whenStable();
    expect(
      el
        .querySelector('[data-testid="ws-row-hub"]')!
        .getAttribute('aria-selected'),
    ).toBe('true');
    expect(
      el
        .querySelector('[data-testid="ws-row-winter"]')!
        .getAttribute('aria-selected'),
    ).toBe('false');
  });

  it('renders the empty copy only once the read settles empty', async () => {
    const empty = await mount({ rows: [], state: 'empty' });
    expect((empty.nativeElement as HTMLElement).querySelector('[data-testid="ws-empty"]')?.textContent).toContain(
      'No active work sources.',
    );
  });

  it('renders no empty copy while loading', async () => {
    const loading = await mount({ rows: [], state: 'loading' });
    expect((loading.nativeElement as HTMLElement).querySelector('[data-testid="ws-empty"]')).toBeNull();
  });
});
