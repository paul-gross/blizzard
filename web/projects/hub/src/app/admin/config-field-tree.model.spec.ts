import {
  type ConfigFieldView,
  defaultChoiceOf,
  dependentsOf,
  fieldChanges,
  optionsOf,
  shownFieldViews,
} from './config-field-tree.model';

interface Draft {
  name: string;
  kind: string;
  flavor: string;
  detail: string;
  count: string;
  mark: string;
  tint: string;
  on: boolean;
}

const FIELDS: readonly ConfigFieldView<Draft>[] = [
  { key: 'name', label: 'Name', kind: 'text', createOnly: true },
  {
    key: 'kind',
    label: 'Kind',
    kind: 'select',
    options: [
      { value: 'a', label: 'Alpha' },
      { value: 'b', label: 'Beta' },
    ],
  },
  {
    key: 'flavor',
    label: 'Flavor',
    kind: 'select',
    filterBy: 'kind',
    options: [
      { value: 'a1', label: 'A one', kind: 'a' },
      { value: 'b1', label: 'B one', kind: 'b' },
    ],
  },
  { key: 'detail', label: 'Detail', kind: 'text', shownWhen: { key: 'kind', value: 'b' } },
  { key: 'count', label: 'Count', kind: 'number' },
  { key: 'mark', label: 'Mark', kind: 'mark' },
  {
    key: 'tint',
    label: 'Tint',
    kind: 'color',
    options: [
      { value: 'red', label: 'Red' },
      { value: 'blue', label: 'Blue' },
    ],
  },
  { key: 'on', label: 'On', kind: 'checkbox' },
];

const BASE: Draft = { name: 'n', kind: 'a', flavor: 'a1', detail: '', count: '5', mark: '', tint: 'red', on: false };

describe('config field tree model', () => {
  it('shows the fields a mode and the draft allow', () => {
    expect(shownFieldViews(FIELDS, 'create', BASE).map((f) => f.key)).toEqual(['name', 'kind', 'flavor', 'count', 'mark', 'tint', 'on']);
    expect(shownFieldViews(FIELDS, 'edit', { ...BASE, kind: 'b' }).map((f) => f.key)).toEqual([
      'kind',
      'flavor',
      'detail',
      'count',
      'mark',
      'tint',
      'on',
    ]);
  });

  it('offers a dependent field only the options its parent selects', () => {
    const flavor = FIELDS[2];
    expect(optionsOf(flavor, BASE).map((o) => o.value)).toEqual(['a1']);
    expect(optionsOf(flavor, { ...BASE, kind: 'b' }).map((o) => o.value)).toEqual(['b1']);
    expect(optionsOf(FIELDS[1], BASE)).toHaveLength(2);
  });

  it('names the transitive dependents of a key', () => {
    const chain: readonly ConfigFieldView<Draft>[] = [
      ...FIELDS,
      { key: 'detail', label: 'Detail', kind: 'select', filterBy: 'flavor' },
    ];
    expect(dependentsOf(chain, 'kind')).toEqual(['flavor', 'detail']);
    expect(dependentsOf(FIELDS, 'name')).toEqual([]);
  });

  it('diffs against the baseline: trimmed text, blank as none, number by value, labels', () => {
    expect(fieldChanges(FIELDS, BASE, { ...BASE, name: 'changed', count: '05' })).toEqual([]);
    const diff = fieldChanges(FIELDS, BASE, { ...BASE, kind: 'b', flavor: 'b1', count: ' 7 ', on: true });
    expect(diff.map((c) => c.field)).toEqual(['Kind', 'Flavor', 'Count', 'On']);
    expect(diff[0]).toMatchObject({ old: 'a', new: 'b', old_label: 'Alpha', new_label: 'Beta' });
    expect(diff[2]).toMatchObject({ old: '5', new: '7' });
    expect(diff[3]).toMatchObject({ old: false, new: true });
  });

  it('names a color change by its option labels', () => {
    const [change] = fieldChanges(FIELDS, BASE, { ...BASE, tint: 'blue' });
    expect(change).toMatchObject({ field: 'Tint', old_label: 'Red', new_label: 'Blue' });
  });

  it('reads a cleared text as none, a hidden field as unchanged, and an image mark as Image', () => {
    const diff = fieldChanges(FIELDS, { ...BASE, count: '5', mark: '🙂' }, { ...BASE, count: '  ', mark: 'data:image/png;base64,AA' });
    expect(diff.find((c) => c.field === 'Count')).toMatchObject({ old: '5', new: null });
    expect(diff.find((c) => c.field === 'Mark')).toMatchObject({ old: '🙂', new_label: 'Image', old_label: null });
    expect(fieldChanges(FIELDS, BASE, { ...BASE, detail: 'x' })).toEqual([]);
  });

  it('labels marks according to the field image source', () => {
    const field = { key: 'mark', label: 'Mark', kind: 'mark', markImageSource: 'data' } as const;
    expect(fieldChanges([field], BASE, { ...BASE, mark: '/🔥' })[0].new_label).toBeNull();
    expect(fieldChanges([field], BASE, { ...BASE, mark: 'data:image/png;base64,AA' })[0].new_label).toBe('Image');
    expect(fieldChanges([{ ...field, markImageSource: 'url' }], BASE, { ...BASE, mark: '/icons/a.png' })[0].new_label).toBe('Image');
  });
});

describe('a number field with a default choice', () => {
  interface Timed {
    hours: string;
    useDefault: boolean;
  }
  const TIMED: readonly ConfigFieldView<Timed>[] = [
    { key: 'hours', label: 'Hours', kind: 'number', orDefault: { key: 'useDefault', label: 'Default' } },
  ];

  it('names its choice, and a field without one has none', () => {
    expect(defaultChoiceOf(TIMED[0])).toEqual({ key: 'useDefault', label: 'Default' });
    expect(defaultChoiceOf(FIELDS[4])).toBeNull();
  });

  it('reads the default kept as no change, whatever the text', () => {
    expect(fieldChanges(TIMED, { hours: '', useDefault: true }, { hours: '12', useDefault: true })).toEqual([]);
  });

  it('names a move to or from the default by its label', () => {
    expect(fieldChanges(TIMED, { hours: '', useDefault: true }, { hours: ' 6 ', useDefault: false })).toEqual([
      { field: 'Hours', old: null, new: '6', old_label: 'Default', new_label: null },
    ]);
    expect(fieldChanges(TIMED, { hours: '6', useDefault: false }, { hours: '6', useDefault: true })).toEqual([
      { field: 'Hours', old: '6', new: null, old_label: null, new_label: 'Default' },
    ]);
  });

  it('compares stated hours by value while the default is not chosen', () => {
    expect(fieldChanges(TIMED, { hours: '6', useDefault: false }, { hours: '06', useDefault: false })).toEqual([]);
    expect(fieldChanges(TIMED, { hours: '6', useDefault: false }, { hours: '7', useDefault: false })).toHaveLength(1);
  });
});
