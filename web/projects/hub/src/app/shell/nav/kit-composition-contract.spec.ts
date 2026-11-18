import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { KitChip, KitChipGroup, KitChipOptionDirective, KitToolbar, KitToolbarWidget } from 'fleet';

@Component({
  imports: [KitChip, KitChipGroup, KitChipOptionDirective, KitToolbar, KitToolbarWidget],
  template: `
    <div fleetKitChipGroup orientation="horizontal" selectionMode="follow" aria-label="Project"
      [value]="project()" [disabled]="disabled()" (valueChange)="select($event)">
      <fleet-kit-chip fleetKitChipOption [value]="null" label="All">All</fleet-kit-chip>
      <fleet-kit-chip fleetKitChipOption value="alpha" label="Alpha">Alpha</fleet-kit-chip>
      <fleet-kit-chip fleetKitChipOption value="blocked" label="Blocked" [disabled]="true">Blocked</fleet-kit-chip>
    </div>
    <div fleetKitToolbar aria-label="Actions" [disabled]="disabled()">
      <fleet-kit-chip fleetKitToolbarWidget (invoked)="actions.push('open')">Open</fleet-kit-chip>
      <fleet-kit-chip fleetKitToolbarWidget [disabled]="true" (invoked)="actions.push('archive')">Archive</fleet-kit-chip>
    </div>
  `,
})
class Consumer {
  readonly project = signal<unknown>(null);
  readonly disabled = signal(false);
  readonly selections: unknown[] = [];
  readonly actions: string[] = [];

  select(value: unknown): void {
    this.selections.push(value);
    this.project.set(value);
  }
}

describe('public kit composition contract', () => {
  beforeEach(() => TestBed.configureTestingModule({ imports: [Consumer], providers: [provideZonelessChangeDetection()] }));

  it('controls projected choices independently of toolbar actions and refuses disabled interactions', async () => {
    const fixture = TestBed.createComponent(Consumer);
    await fixture.whenStable();
    const host = fixture.nativeElement as HTMLElement;
    const options = host.querySelectorAll<HTMLElement>('[role=option]');
    const actions = host.querySelectorAll<HTMLElement>('[role=toolbar] [role=button]');
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    options[1].click();
    await fixture.whenStable();
    expect(fixture.componentInstance.selections).toEqual(['alpha']);
    expect(options[1].getAttribute('aria-selected')).toBe('true');
    options[2].click();
    actions[0].click();
    actions[1].click();
    expect(fixture.componentInstance.selections).toEqual(['alpha']);
    expect(fixture.componentInstance.actions).toEqual(['open']);

    fixture.componentInstance.project.set(null);
    fixture.componentInstance.disabled.set(true);
    await fixture.whenStable();
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    options[1].click();
    actions[0].click();
    expect(fixture.componentInstance.selections).toEqual(['alpha']);
    expect(fixture.componentInstance.actions).toEqual(['open']);
  });
});
