import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GateBadgeGroup } from './gate-badge-group';

async function render(gates: readonly string[]): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(GateBadgeGroup);
  fixture.componentRef.setInput('gates', gates);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('GateBadgeGroup', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GateBadgeGroup],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the label and one badge per gated node name', async () => {
    const el = await render(['build', 'review']);

    expect(el.querySelector('.gates-label')?.textContent?.trim()).toBe('Gates:');
    const badges = el.querySelectorAll('[data-testid="runner-gate-badge"]');
    expect(badges).toHaveLength(2);
    expect([...badges].map((b) => b.textContent?.trim())).toEqual(['build', 'review']);
    expect(badges[0].getAttribute('data-gate')).toBe('build');
  });

  it('renders nothing for a runner that imposes no gates', async () => {
    const el = await render([]);

    expect(el.querySelector('[data-testid="runner-gates"]')).toBeNull();
    expect(el.querySelector('[data-testid="runner-gate-badge"]')).toBeNull();
  });
});
