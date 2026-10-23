import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { MobileTabBar } from './mobile-tab-bar';

describe('MobileTabBar', () => {
  async function render() {
    await TestBed.configureTestingModule({
      imports: [MobileTabBar],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(MobileTabBar);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders Board, Fleet, Events and Gardening, in that order, and no Asks tab', async () => {
    const el = await render();

    const tabs = Array.from(el.querySelectorAll('[data-testid^="tab-"]')).map((tab) => tab.getAttribute('data-testid'));
    expect(tabs).toEqual(['tab-board', 'tab-fleet', 'tab-events', 'tab-gardening']);
  });

  it('routes every tab', async () => {
    const el = await render();

    for (const [testid, href] of [
      ['tab-board', '/board'],
      ['tab-fleet', '/fleet'],
      ['tab-events', '/events'],
      ['tab-gardening', '/gardening'],
    ]) {
      const tab = el.querySelector(`[data-testid="${testid}"]`);
      expect(tab?.getAttribute('href')).toBe(href);
      expect(tab?.classList.contains('inert')).toBe(false);
    }
  });
});
