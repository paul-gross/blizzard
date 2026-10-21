import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitErrorLine } from './kit-error-line';

@Component({
  selector: 'fleet-test-host',
  imports: [KitErrorLine],
  template: `<fleet-kit-error-line [message]="message()" testid="the-error" />`,
})
class TestHost {
  readonly message = signal<string | null>(null);
}

describe('KitErrorLine', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders nothing while there is no message', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.line')).toBeNull();
    expect(el.querySelector('[role="alert"]')).toBeNull();
  });

  it('renders the message as an alert carrying the testid once there is one', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.message.set('Color changed elsewhere.');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const line = el.querySelector('[data-testid="the-error"]') as HTMLElement;
    expect(line.getAttribute('role')).toBe('alert');
    expect(line.textContent?.trim()).toBe('Color changed elsewhere.');
  });

  it('removes the line again when the message clears', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.message.set('Nope.');
    await fixture.whenStable();
    fixture.componentInstance.message.set(null);
    await fixture.whenStable();

    expect((fixture.nativeElement as HTMLElement).querySelector('.line')).toBeNull();
  });
});
