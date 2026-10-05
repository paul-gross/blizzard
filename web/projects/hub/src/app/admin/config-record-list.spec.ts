import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ConfigRecordList, type ConfigRowVm } from './config-record-list';

const ROWS: readonly ConfigRowVm[] = [
  { key: 'hub', title: 'hub', sub: [], badges: [{ label: 'built-in', tone: 'spawning' }], revision: null, retired: false },
  { key: 'winter', title: 'winter', sub: ['github', 'paul-gross/winter'], badges: [], revision: 2, retired: false },
];

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

  it('emits the picked key', async () => {
    const fixture = await mount({});
    const picked: string[] = [];
    fixture.componentInstance.pick.subscribe((key) => picked.push(key));
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="ws-row-hub"]')!.click();
    expect(picked).toEqual(['hub']);
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
