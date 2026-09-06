import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { SubscriptionPace } from './runner-panel';
import { SubscriptionPaceGroup } from './subscription-pace-group';

async function render(subscriptionPaces: readonly SubscriptionPace[]): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(SubscriptionPaceGroup);
  fixture.componentRef.setInput('subscriptionPaces', subscriptionPaces);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('SubscriptionPaceGroup', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SubscriptionPaceGroup],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('keeps two subscriptions sharing an identical window label distinct, each under its own slug', async () => {
    const el = await render([
      {
        slug: 'anthropic-default',
        name: 'Anthropic (default)',
        paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 20 }],
      },
      {
        slug: 'anthropic-secondary',
        name: 'Anthropic (secondary)',
        paceBars: [{ window: '5h', utilizationPct: 90, elapsedPct: 55 }],
      },
    ]);

    const groups = el.querySelectorAll('[data-testid="subscription-pace-group"]');
    expect(groups).toHaveLength(2);

    const defaultGroup = el.querySelector('[data-subscription-slug="anthropic-default"]');
    const secondaryGroup = el.querySelector('[data-subscription-slug="anthropic-secondary"]');
    expect(defaultGroup?.querySelector('[data-testid="subscription-pace-group-name"]')?.textContent).toContain(
      'Anthropic (default)',
    );
    expect(secondaryGroup?.querySelector('[data-testid="subscription-pace-group-name"]')?.textContent).toContain(
      'Anthropic (secondary)',
    );

    // Both groups report a "5h" window — each stays scoped to its own group rather
    // than merging into one shared bar list.
    expect(defaultGroup?.querySelectorAll('[data-pace-window="5h"]')).toHaveLength(1);
    expect(secondaryGroup?.querySelectorAll('[data-pace-window="5h"]')).toHaveLength(1);
    expect(
      defaultGroup?.querySelector('[data-pace-window="5h"] [data-testid="pace-bar-utilization"] .fill')?.getAttribute(
        'style',
      ),
    ).toContain('width: 40%');
    expect(
      secondaryGroup
        ?.querySelector('[data-pace-window="5h"] [data-testid="pace-bar-utilization"] .fill')
        ?.getAttribute('style'),
    ).toContain('width: 90%');
  });

  it('renders a declared subscription with no sample yet as unsampled, never a fabricated zero bar', async () => {
    const el = await render([{ slug: 'anthropic-default', name: 'Anthropic (default)', paceBars: [] }]);

    const group = el.querySelector('[data-subscription-slug="anthropic-default"]');
    expect(group?.querySelector('[data-testid="runner-pace-bar"]')).toBeNull();
    const unsampled = group?.querySelector('[data-testid="subscription-pace-group-unsampled"]');
    expect(unsampled).not.toBeNull();
    expect(unsampled?.getAttribute('aria-label')).toBe('Anthropic (default) usage not yet sampled');
  });

  it('renders nothing at all for a runner with no declared subscriptions', async () => {
    const el = await render([]);

    expect(el.querySelector('[data-testid="subscription-pace-group"]')).toBeNull();
  });
});
