import { Tab, TabList, Tabs } from '@angular/aria/tabs';
import { Component, provideZonelessChangeDetection, viewChild } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { setTabStop } from './aria-tab-stop';

@Component({
  selector: 'fleet-test-host',
  imports: [Tabs, TabList, Tab],
  template: `
    <div ngTabs>
      <div ngTabList selectedTab="alpha">
        <button ngTab value="alpha" type="button" data-testid="alpha">Alpha</button>
        <button ngTab value="bravo" type="button" data-testid="bravo">Bravo</button>
      </div>
    </div>
  `,
})
class TestHost {
  readonly list = viewChild.required(TabList);
}

/**
 * Pins the kit's one access to Aria's private `_pattern`: no public `TabList` call moves the roving
 * stop without focusing, so an Aria upgrade that moves the private field has to fail here. The first
 * assertion on the stale stop also pins why the helper exists — when it fails, Aria follows an
 * outside selection by itself and the helper can go.
 */
describe('setTabStop', () => {
  it('moves the Tab stop that Aria leaves behind on an outside selection after interaction', async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const tab = (id: string) => el.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
    const list = fixture.componentInstance.list();

    tab('alpha').focus();
    tab('alpha').dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    await fixture.whenStable();
    list.selectedTab.set('bravo');
    await fixture.whenStable();
    expect(tab('alpha').getAttribute('tabindex')).toBe('0');

    setTabStop(list, 'bravo');
    await fixture.whenStable();
    expect(tab('bravo').getAttribute('tabindex')).toBe('0');
    expect(tab('alpha').getAttribute('tabindex')).toBe('-1');
    expect(document.activeElement).toBe(tab('alpha'));
  });
});
