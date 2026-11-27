import type { TabList } from '@angular/aria/tabs';

/**
 * Puts `list`'s roving Tab stop on the tab valued `value`, without focus; no-op when no tab has it.
 * Aria moves the tab stop to the selected tab only before first interaction
 * (https://github.com/angular/components/issues/34182). Remove this when that is fixed.
 */
export function setTabStop(list: TabList, value: string | undefined): void {
  const target = list.findTab(value)?._pattern;
  if (target !== undefined && list._pattern.inputs.activeItem() !== target) list._pattern.inputs.activeItem.set(target);
}
