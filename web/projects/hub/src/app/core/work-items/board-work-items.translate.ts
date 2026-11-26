import type { WorkItemsView } from 'fleet/hub-api';
import type { BoardWorkItems } from './board-work-items.domain';

export function fromBoardWorkItems(view: WorkItemsView): BoardWorkItems {
  return {
    items: view.items.map(({ source, ref, title, label, web_url }) => ({ source, ref, title, label, web_url })),
  };
}
