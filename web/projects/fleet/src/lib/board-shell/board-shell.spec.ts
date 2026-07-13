import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { BoardShell } from './board-shell';

describe('BoardShell', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the board shell with all five columns and an empty state', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();
    expect(el.querySelectorAll('[data-col]')).toHaveLength(5);
    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('NO CHUNKS');
  });

  it('reflects the connection input in the header', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('connection', 'ok');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('ok');
  });
});
