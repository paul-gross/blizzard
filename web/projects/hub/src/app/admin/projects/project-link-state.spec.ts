import { Component, DestroyRef, inject } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { injectProjectLinkFormFactory } from './project-link-state';

@Component({ template: '' })
class Host {
  readonly create = injectProjectLinkFormFactory();
}

describe('project link form lifetime', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('destroys the superseded opening and the current opening with the component', () => {
    const fixture = TestBed.createComponent(Host);
    const firstDestroyed = vi.fn();
    const secondDestroyed = vi.fn();
    fixture.componentInstance.create(() => {
      inject(DestroyRef).onDestroy(firstDestroyed);
      return [];
    });
    fixture.componentInstance.create(() => {
      inject(DestroyRef).onDestroy(secondDestroyed);
      return [];
    });
    expect(firstDestroyed).toHaveBeenCalledOnce();
    expect(secondDestroyed).not.toHaveBeenCalled();
    fixture.destroy();
    expect(secondDestroyed).toHaveBeenCalledOnce();
  });
});
