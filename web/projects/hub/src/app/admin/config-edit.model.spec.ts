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
      { field: 'Locator', old: 'o/r', new: 'o/r2' },
      { field: 'Annotates', old: false, new: true },
    ]);
  });

  it('sends a cleared input as null', () => {
    const edit = configEdit(FIELDS, shown, { ...shown, web_base: '  ' });
    expect(edit.body).toEqual({ web_base: null });
    expect(edit.diff).toEqual([{ field: 'Web base', old: 'https://x', new: null }]);
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
