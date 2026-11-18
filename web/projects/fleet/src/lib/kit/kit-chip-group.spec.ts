import { Listbox } from '@angular/aria/listbox';
import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { KitChipGroup } from './kit-chip-group';
import { KitChipOptionDirective } from './kit-chip-option';
import { KitChip } from './kit-chips';

@Component({
  imports: [KitChipGroup, KitChipOptionDirective, KitChip],
  template: `<div fleetKitChipGroup orientation="horizontal" [selectionMode]="hasSelection() ? 'follow' : 'explicit'" aria-label="Scope" [value]="value()" [hasSelection]="hasSelection()" (valueChange)="changes.push($event)">
    <fleet-kit-chip fleetKitChipOption [value]="null" label="All">All</fleet-kit-chip>
    <fleet-kit-chip fleetKitChipOption [value]="optionValue()" label="A">A</fleet-kit-chip>
  </div>`,
})
class Host {
  readonly value = signal<string | null>(null);
  readonly optionValue = signal('a');
  readonly hasSelection = signal(true);
  changes: unknown[] = [];
}

describe('KitChipGroup', () => {
  it('configures horizontal follow selection and distinguishes null from no selection', async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const listbox = fixture.debugElement.query(By.directive(Listbox)).injector.get(Listbox);
    expect(listbox.orientation()).toBe('horizontal');
    expect(listbox.selectionMode()).toBe('follow');
    const options = fixture.nativeElement.querySelectorAll('[role=option]') as NodeListOf<HTMLElement>;
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    fixture.componentInstance.hasSelection.set(false);
    await fixture.whenStable();
    expect(options[0].getAttribute('aria-selected')).toBe('false');
    fixture.componentInstance.value.set('a');
    fixture.componentInstance.hasSelection.set(true);
    await fixture.whenStable();
    expect(options[1].getAttribute('aria-selected')).toBe('true');
    options[0].click();
    expect(fixture.componentInstance.changes).toEqual([null]);
  });
  it('starts empty in explicit mode and selects the null option on keyboard activation', async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.hasSelection.set(false);
    await fixture.whenStable();
    const options = fixture.nativeElement.querySelectorAll('[role=option]') as NodeListOf<HTMLElement>;
    expect(options[0].getAttribute('aria-selected')).toBe('false');
    options[0].focus();
    options[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(fixture.componentInstance.changes).toEqual([null]);
  });

  it('tracks changes to projected option and controlled values without emitting', async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.value.set('a');
    await fixture.whenStable();
    const options = fixture.nativeElement.querySelectorAll('[role=option]') as NodeListOf<HTMLElement>;
    expect(options[1].getAttribute('aria-selected')).toBe('true');
    fixture.componentInstance.optionValue.set('b');
    fixture.componentInstance.value.set('b');
    await fixture.whenStable();
    expect(options[1].getAttribute('aria-selected')).toBe('true');
    expect(fixture.componentInstance.changes).toEqual([]);
    options[0].click();
    options[1].click();
    expect(fixture.componentInstance.changes).toEqual([null, 'b']);
  });

});
