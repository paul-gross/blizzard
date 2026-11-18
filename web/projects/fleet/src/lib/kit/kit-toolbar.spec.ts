import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { KitToolbar, KitToolbarWidget } from './kit-toolbar';

@Component({
  imports: [KitToolbar, KitToolbarWidget],
  template: `<div fleetKitToolbar [disabled]="disabled()" [orientation]="orientation()" aria-label="Actions">
    <span fleetKitToolbarWidget (invoked)="count = count + 1">Accept</span>
    <span fleetKitToolbarWidget (invoked)="count = count + 1">Decline</span>
    <span fleetKitToolbarWidget [disabled]="true" (invoked)="count = count + 1">Unavailable</span>
  </div>`,
})
class Host {
  readonly disabled = signal(false);
  readonly orientation = signal<'horizontal' | 'vertical'>('horizontal');
  count = 0;
}

describe('KitToolbar', () => {
  beforeEach(() => TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }));

  it('invokes once for click and keyboard, and refuses disabled actions', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const widgets = (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('[role=button]');
    widgets[0].click();
    widgets[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    widgets[0].dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    expect(fixture.componentInstance.count).toBe(3);
    widgets[2].click();
    expect(fixture.componentInstance.count).toBe(3);
    fixture.componentInstance.disabled.set(true);
    await fixture.whenStable();
    widgets[0].click();
    expect(fixture.componentInstance.count).toBe(3);
  });
  it('moves focus with arrows without invoking an action', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const widgets = (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('[role=button]');
    widgets[0].focus();
    widgets[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(document.activeElement).toBe(widgets[1]);
    expect(fixture.componentInstance.count).toBe(0);
  });

  it('exposes vertical orientation and navigates with vertical arrows', async () => {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.orientation.set('vertical');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[role=toolbar]')?.getAttribute('aria-orientation')).toBe('vertical');
    const widgets = el.querySelectorAll<HTMLElement>('[role=button]');
    widgets[0].focus();
    widgets[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    expect(document.activeElement).toBe(widgets[1]);
    expect(fixture.componentInstance.count).toBe(0);
  });

});
