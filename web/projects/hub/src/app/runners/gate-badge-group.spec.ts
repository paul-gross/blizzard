import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { GateBadgeGroup } from './gate-badge-group';

async function render(gates: readonly string[], labelled = true): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(GateBadgeGroup);
  fixture.componentRef.setInput('gates', gates);
  fixture.componentRef.setInput('labelled', labelled);
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

  it('drops its label where a surrounding grid labels it', async () => {
    const el = await render(['build'], false);

    expect(el.querySelector('.gates-label')).toBeNull();
    expect(el.querySelectorAll('[data-testid="runner-gate-badge"]')).toHaveLength(1);
  });

  it('renders nothing for a runner that imposes no gates', async () => {
    const el = await render([]);

    expect(el.querySelector('[data-testid="runner-gates"]')).toBeNull();
    expect(el.querySelector('[data-testid="runner-gate-badge"]')).toBeNull();
  });
});
