import {
  type ConfigFieldDef,
  configEdit,
  createBody,
  editableFields,
  emptyFormValues,
  formValuesOf,
  missingRequired,
  shownFormValues,
} from './config-edit.model';

const FIELDS: readonly ConfigFieldDef[] = [
  {
    key: 'name',
    label: 'Name',
    kind: 'text',
    required: true,
    createOnly: true,
  },
  { key: 'locator', label: 'Locator', kind: 'text', required: true },
  { key: 'web_base', label: 'Web base', kind: 'text' },
  { key: 'annotate', label: 'Annotates', kind: 'checkbox' },
];

describe('formValuesOf', () => {
  it('reads text fields as text and checkboxes as flags, an unset field as empty', () => {
    expect(
      formValuesOf(FIELDS, {
        name: 'a',
        locator: 'o/r',
        web_base: null,
        annotate: true,
      }),
    ).toEqual({
      name: 'a',
      locator: 'o/r',
      web_base: '',
      annotate: true,
    });
  });
});

describe('configEdit', () => {
  const shown = formValuesOf(FIELDS, {
    name: 'a',
    locator: 'o/r',
    web_base: 'https://x',
    annotate: false,
  });

  it('labels a select change with the chosen option, so the preview matches the saved revision', () => {
    const fields: readonly ConfigFieldDef[] = [
      {
        key: 'forge',
        label: 'Forge',
        kind: 'select',
        options: [
          { value: 'frg_1', label: 'gh · GitHub' },
          { value: 'frg_2', label: 'gl · GitLab' },
        ],
      },
    ];
    expect(configEdit(fields, { forge: 'frg_1' }, { forge: 'frg_2' }).diff).toEqual([
      { field: 'Forge', old: 'frg_1', new: 'frg_2', old_label: 'gh · GitHub', new_label: 'gl · GitLab' },
    ]);
  });

  it('is empty when the form matches the record', () => {
    expect(configEdit(FIELDS, shown, shown)).toEqual({ body: {}, diff: [] });
  });

  it('sends only the changed fields, as the patch and as the diff', () => {
    const edit = configEdit(FIELDS, shown, {
      ...shown,
      locator: ' o/r2 ',
      annotate: true,
    });
    expect(edit.body).toEqual({ locator: 'o/r2', annotate: true });
    expect(edit.diff).toEqual([
       { field: 'Locator', old: 'o/r', new: 'o/r2', old_label: null, new_label: null },
       { field: 'Annotates', old: false, new: true, old_label: null, new_label: null },
    ]);
  });

  it('sends a cleared input as null', () => {
    const edit = configEdit(FIELDS, shown, { ...shown, web_base: '  ' });
    expect(edit.body).toEqual({ web_base: null });
    expect(edit.diff).toEqual([{ field: 'Web base', old: 'https://x', new: null, old_label: null, new_label: null }]);
  });

  it('never edits a create-only field', () => {
    expect(configEdit(FIELDS, shown, { ...shown, name: 'b' }).body).toEqual({});
    expect(editableFields(FIELDS).map((field) => field.key)).not.toContain('name');
  });
});

describe('missingRequired and createBody', () => {
  it('names the required fields left empty', () => {
    expect(missingRequired(FIELDS, emptyFormValues(FIELDS))).toEqual(['Name', 'Locator']);
    expect(
      missingRequired(FIELDS, {
        ...emptyFormValues(FIELDS),
        name: 'a',
        locator: 'x',
      }),
    ).toEqual([]);
  });

  it('omits empty text and keeps flags, trimmed', () => {
    expect(
      createBody(FIELDS, {
        name: ' a ',
        locator: 'o/r',
        web_base: '',
        annotate: false,
      }),
    ).toEqual({
      name: 'a',
      locator: 'o/r',
      annotate: false,
    });
  });
});

describe('shownFormValues', () => {
  it('is null with no record, and the record as form values otherwise', () => {
    expect(shownFormValues(FIELDS, undefined)).toBeNull();
    expect(shownFormValues(FIELDS, { locator: 'o/r' })?.['locator']).toBe('o/r');
  });
});
