import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { userEvent } from 'vitest/browser';

import { KitGridFixture } from './kit-grid.fixture';

describe('KitGrid bridge shell sweep', () => {
  async function mount() {
    await TestBed.configureTestingModule({
      imports: [KitGridFixture],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(KitGridFixture);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    return { fixture, root };
  }
  const q = (root: HTMLElement, testid: string) => root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!;
  const settle = (fixture: { whenStable(): Promise<unknown> }) =>
    new Promise<void>((done) => setTimeout(() => fixture.whenStable().then(() => done()), 80));
  const popup = () => document.querySelector<HTMLElement>('[role="listbox"]');
  const press = async (fixture: { whenStable(): Promise<unknown> }, keys: string) => {
    await userEvent.keyboard(keys);
    await settle(fixture);
  };
  const run = async (body: (ctx: Awaited<ReturnType<typeof mount>>) => Promise<void>) => {
    const ctx = await mount();
    try {
      await body(ctx);
    } finally {
      ctx.fixture.destroy();
      ctx.root.remove();
    }
  };

  it('is one Tab stop, and arrow keys move between text cells and controls, skipping disabled cells', () =>
    run(async ({ fixture, root }) => {
      q(root, 'grid-before').focus();
      await press(fixture, '{Tab}');
      const grid = q(root, 'grid');
      expect(grid.contains(document.activeElement)).toBe(true);
      expect(document.activeElement?.textContent?.trim()).toBe('ada');

      await press(fixture, '{Tab}');
      expect(document.activeElement).toBe(q(root, 'grid-after'));

      await press(fixture, '{Shift>}{Tab}{/Shift}');
      expect(document.activeElement?.textContent?.trim()).toBe('ada');

      await press(fixture, '{ArrowRight}');
      expect(document.activeElement).toBe(q(root, 'grid-select-ada'));
      expect(document.activeElement?.tagName).toBe('BUTTON');
      await press(fixture, '{ArrowRight}');
      expect(document.activeElement).toBe(q(root, 'grid-button-ada'));

      // Tab from a control leaves the grid in one stop.
      await press(fixture, '{Tab}');
      expect(document.activeElement).toBe(q(root, 'grid-after'));
      await press(fixture, '{Shift>}{Tab}{/Shift}');
      expect(document.activeElement).toBe(q(root, 'grid-button-ada'));

      // Down skips the second row's disabled button; the text cell is reachable.
      await press(fixture, '{ArrowLeft}{ArrowLeft}{ArrowDown}');
      expect(document.activeElement?.textContent?.trim()).toBe('grace');
      await press(fixture, '{ArrowRight}');
      expect(document.activeElement?.textContent?.trim()).toBe('grace');
      expect(q(root, 'grid-select-grace').getAttribute('tabindex')).toBe('-1');
      expect(q(root, 'grid-button-grace').getAttribute('tabindex')).toBe('-1');
    }));

  it('one Enter or one Space on an enabled button is exactly one click; a disabled button gives none', () =>
    run(async ({ fixture, root }) => {
      q(root, 'grid-button-ada').focus();
      await settle(fixture);
      await press(fixture, '{Enter}');
      expect(q(root, 'grid-clicks').textContent).toBe('1');
      await press(fixture, ' ');
      expect(q(root, 'grid-clicks').textContent).toBe('2');
      expect(document.activeElement).toBe(q(root, 'grid-button-ada'));
      // A disabled button cannot take focus, so no key reaches it.
      const disabled = q(root, 'grid-button-grace') as HTMLButtonElement;
      expect(disabled.disabled).toBe(true);
      disabled.focus();
      expect(document.activeElement).not.toBe(disabled);
      disabled.click();
      expect(q(root, 'grid-clicks').textContent).toBe('2');
    }));

  for (const [name, key] of [['Enter', '{Enter}'], ['Space', ' ']] as const) {
    it(`${name} opens the select popup once, arrows move the option, and Escape returns focus to the trigger`, () =>
      run(async ({ fixture, root }) => {
        const trigger = q(root, 'grid-select-ada');
        trigger.focus();
        await settle(fixture);
        await press(fixture, key);
        expect(document.querySelectorAll('[role="listbox"]').length).toBe(1);
        expect(trigger.getAttribute('aria-expanded')).toBe('true');
        expect(document.activeElement?.getAttribute('role')).toBe('option');
        expect(document.activeElement?.textContent?.trim()).toBe('guest');

        await press(fixture, '{ArrowDown}');
        expect(document.activeElement?.textContent?.trim()).toBe('contributor');
        expect(popup()).not.toBeNull();

        await press(fixture, '{Escape}');
        expect(popup()).toBeNull();
        expect(trigger.getAttribute('aria-expanded')).toBe('false');
        expect(document.activeElement).toBe(trigger);
        // Escape did not also navigate the grid: the active cell is still the select.
        expect(trigger.getAttribute('tabindex')).toBe('0');
      }));
  }

  it('a pick closes the popup, emits the choice once, and returns focus to the same trigger', () =>
    run(async ({ fixture, root }) => {
      const trigger = q(root, 'grid-select-ada');
      trigger.focus();
      await settle(fixture);
      await press(fixture, ' ');
      await press(fixture, '{ArrowDown}');
      await press(fixture, '{Enter}');
      expect(popup()).toBeNull();
      expect(fixture.componentInstance.choices()).toEqual(['ada:contributor']);
      expect(document.activeElement).toBe(trigger);
      // Back in the grid, the arrow keys move again.
      await press(fixture, '{ArrowRight}');
      expect(document.activeElement).toBe(q(root, 'grid-button-ada'));
    }));

  it('an outside click closes the popup and deactivates the widget', () =>
    run(async ({ fixture, root }) => {
      const trigger = q(root, 'grid-select-ada');
      trigger.focus();
      await settle(fixture);
      await press(fixture, '{Enter}');
      expect(popup()).not.toBeNull();
      document.querySelector<HTMLElement>('.cdk-overlay-backdrop')!.click();
      await settle(fixture);
      expect(popup()).toBeNull();
      expect(document.activeElement).toBe(trigger);
      await press(fixture, '{ArrowRight}');
      expect(document.activeElement).toBe(q(root, 'grid-button-ada'));
    }));

  it('a pointer click opens the popup and keeps the widget in step', () =>
    run(async ({ fixture, root }) => {
      const trigger = q(root, 'grid-select-ada');
      await userEvent.click(trigger);
      await settle(fixture);
      expect(popup()).not.toBeNull();
      expect(document.activeElement?.getAttribute('role')).toBe('option');
      await press(fixture, '{Escape}');
      expect(popup()).toBeNull();
      expect(document.activeElement).toBe(trigger);
    }));
});
