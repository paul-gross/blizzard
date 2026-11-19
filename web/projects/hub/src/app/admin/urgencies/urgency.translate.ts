import { ProjectColor, RecordKind, type EditBaseline, type RefusalMap, type Urgency, type UrgencyCreateRequest, type UrgencyPatchRequest, type SettingsView } from 'fleet';
import { markIsImage as isImage } from '../config-edit.model';
import type { UrgencyDraft, UrgencySettings } from './urgency.domain';

export function toDraft(urgency: Urgency | undefined, image: string | null | undefined): UrgencyDraft | null {
  if (!urgency || (urgency.icon && !image)) return null;
  return { slug: urgency.slug, name: urgency.name, color: urgency.color, mark: urgency.emoji ?? (urgency.icon ? image! : '') };
}

export function toEditBaseline(urgency: Urgency, draft: UrgencyDraft): EditBaseline<UrgencyDraft> {
  return { id: urgency.id, revision: urgency.revision, draft: { ...draft } };
}

const FIELDS: Readonly<Record<string, keyof UrgencyDraft>> = {
  slug: 'slug', name: 'name', color: 'color', emoji: 'mark', icon: 'mark', 'icon.media_type': 'mark', 'icon.data': 'mark',
};
export const URGENCY_REFUSALS: RefusalMap<UrgencyDraft> = {
  field: (path) => Object.hasOwn(FIELDS, path) ? FIELDS[path] : null,
  kinds: { 'urn:blizzard:problem:urgency-slug-taken': 'slug' },
};
function icon(value: string): { media_type: string; data: string } {
  const [header, data] = value.split(',');
  return { media_type: header.slice(5, header.indexOf(';')), data };
}
function markIsImage(value: string): boolean {
  return isImage(value, 'data');
}
export function toCreateRequest(draft: UrgencyDraft): UrgencyCreateRequest {
  const mark = draft.mark.trim(); const color = draft.color.trim();
  return { slug: draft.slug.trim(), name: draft.name.trim(), ...(color ? { color } : {}),
    ...(mark ? markIsImage(mark) ? { icon: icon(mark) } : { emoji: mark } : {}) };
}
export function toEditRequest(before: UrgencyDraft, after: UrgencyDraft): UrgencyPatchRequest {
  const body: UrgencyPatchRequest = {};
  for (const key of ['name', 'color'] as const) {
    if (before[key].trim() !== after[key].trim()) body[key] = after[key].trim();
  }
  const mark = after.mark.trim();
  if (before.mark.trim() !== mark) {
    body.emoji = mark && !markIsImage(mark) ? mark : null;
    body.icon = mark && markIsImage(mark) ? icon(mark) : null;
  }
  return body;
}

export const URGENCY_COLORS = Object.values(ProjectColor);
export const URGENCY_RECORD_KIND = RecordKind.URGENCY;

export function fromSettingsView(settings: SettingsView): UrgencySettings {
  return { defaultId: settings.default_urgency.urgency_id, revision: settings.revision };
}
