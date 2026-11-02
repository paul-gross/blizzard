import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { UnenforceableGateView } from 'fleet';

import { GateBadgeGroup } from './gate-badge-group';

async function render(
  gates: readonly string[],
  labelled = true,
  unenforceable: readonly UnenforceableGateView[] = [],
): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(GateBadgeGroup);
  fixture.componentRef.setInput('gates', gates);
  fixture.componentRef.setInput('unenforceable', unenforceable);
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

  it.each([
    ['hub-executed', 'deliver'],
    ['unknown-node', 'no-such-node'],
  ] as const)('marks a gate the hub reports %s as not enforceable, in the visible text', async (reason, node) => {
    const el = await render(['build', node], true, [{ node, reason }]);

    const flagged = el.querySelector(`[data-gate="${node}"]`)!;
    expect(flagged.getAttribute('data-unenforceable')).toBe(reason);
    expect(flagged.textContent).toContain(`not enforceable: ${reason}`);
  });

  it('renders an enforceable gate with no marker', async () => {
    const el = await render(['build', 'deliver'], true, [{ node: 'deliver', reason: 'hub-executed' }]);

    const plain = el.querySelector('[data-gate="build"]')!;
    expect(plain.hasAttribute('data-unenforceable')).toBe(false);
    expect(plain.textContent?.trim()).toBe('build');
    expect(el.querySelectorAll('[data-testid="runner-gate-unenforceable"]')).toHaveLength(1);
  });

  it('renders an unrecognized reason raw', async () => {
    const reason = 'some-future-reason' as UnenforceableGateView['reason'];
    const el = await render(['x'], true, [{ node: 'x', reason }]);

    expect(el.querySelector('[data-testid="runner-gate-unenforceable"]')?.textContent?.trim()).toBe(
      'not enforceable: some-future-reason',
    );
  });
});
