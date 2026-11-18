import { URGENCY_COLORS } from './urgency.translate';
import type { ConfigFieldView } from '../config-field-tree.model';
import { apply, required, schema, validate } from '@angular/forms/signals';
import { URGENCY_ICON_MAX_BYTES, urgencyMarkError, type UrgencyDraft } from './urgency.domain';

export const urgencyEditRules = schema<UrgencyDraft>((p) => {
  required(p.name);
  validate(p.mark, ({ value }) => {
    const message = urgencyMarkError(value());
    return message === null ? null : { kind: 'mark', message };
  });
  validate(p.name, ({ value }) => value() !== '' && !value().trim() ? { kind: 'required', message: 'Required' } : null);
});
export const urgencyCreateRules = schema<UrgencyDraft>((p) => {
  apply(p, urgencyEditRules);
  required(p.slug);
  validate(p.slug, ({ value }) => {
    const slug = value().trim();
    if (slug === '') return value() === '' ? null : { kind: 'required', message: 'Required' };
    return slug.length <= 64 && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)
      ? null : { kind: 'slug', message: 'Use 1–64 lowercase letters or digits, separated by single hyphens' };
  });
});

export const URGENCY_FIELDS: readonly ConfigFieldView<UrgencyDraft>[] = [
  { key: 'slug', label: 'Slug', kind: 'text', createOnly: true },
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'color', label: 'Color', kind: 'color', options: URGENCY_COLORS.map((value) => ({ value, label: value })) },
  { key: 'mark', label: 'Mark', kind: 'mark', maxImageBytes: URGENCY_ICON_MAX_BYTES },
];
