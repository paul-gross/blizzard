import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { type SubscriptionRow, LocalSubscriptionsView } from './local-subscriptions-view';

async function render(rows: readonly SubscriptionRow[]) {
  await TestBed.configureTestingModule({
    imports: [LocalSubscriptionsView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalSubscriptionsView);
  fixture.componentRef.setInput('rows', rows);
  fixture.detectChanges();
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement };
}

describe('LocalSubscriptionsView', () => {
  it('renders a subscription with its name, provider, and status — no query stub required', async () => {
    const { el } = await render([
      {
        slug: 'anthropic',
        name: 'Anthropic',
        provider: 'anthropic',
        conditionLabel: 'ok',
        sampledAgo: '30s ago',
        renewalLabel: null,
        ok: true,
      },
    ]);

    const row = el.querySelector('[data-testid="subscription-row"]');
    expect(row?.getAttribute('data-slug')).toBe('anthropic');
    expect(row?.querySelector('.name')?.textContent).toBe('Anthropic');
    expect(row?.querySelector('.provider')?.textContent).toBe('anthropic');
    expect(row?.querySelector('.condition')?.textContent).toBe('ok');
    expect(row?.querySelector('.sampled')?.textContent).toContain('30s ago');
    expect(row?.classList.contains('miss')).toBe(false);
  });

  it('marks a miss row distinctly from an ok row', async () => {
    const { el } = await render([
      {
        slug: 'codex',
        name: 'Codex',
        provider: 'openai',
        conditionLabel: 'miss: credential_lapsed',
        sampledAgo: '2m ago',
        renewalLabel: null,
        ok: false,
      },
    ]);

    const row = el.querySelector('[data-testid="subscription-row"]');
    expect(row?.classList.contains('miss')).toBe(true);
    expect(row?.querySelector('.condition')?.textContent).toBe('miss: credential_lapsed');
  });

  it('renders one row per declared subscription', async () => {
    const { el } = await render([
      {
        slug: 'anthropic',
        name: 'Anthropic',
        provider: 'anthropic',
        conditionLabel: 'ok',
        sampledAgo: '30s ago',
        renewalLabel: null,
        ok: true,
      },
      {
        slug: 'codex',
        name: 'Codex',
        provider: 'openai',
        conditionLabel: 'never sampled',
        sampledAgo: 'never',
        renewalLabel: null,
        ok: null,
      },
    ]);

    expect(el.querySelectorAll('[data-testid="subscription-row"]')).toHaveLength(2);
  });

  it('renders a renewal line only when the row carries a renewal outcome', async () => {
    const { el } = await render([
      {
        slug: 'codex',
        name: 'Codex',
        provider: 'openai',
        conditionLabel: 'ok',
        sampledAgo: '30s ago',
        renewalLabel: 'renewed',
        ok: true,
      },
    ]);

    const row = el.querySelector('[data-testid="subscription-row"]');
    expect(row?.querySelector('[data-testid="subscription-renewal"]')?.textContent).toBe('renewal: renewed');
  });
});
