import { LENS_PICKER_PATH, lensPickerFrom } from './lens-picker.model';

describe('lensPickerFrom', () => {
  it('returns to the view it names, query and all', () => {
    expect(lensPickerFrom('/fleet')).toBe('/fleet');
    expect(lensPickerFrom('/board?layout=list')).toBe('/board?layout=list');
  });

  it('falls back to the board for no view, the root, or a relative path', () => {
    for (const from of [null, undefined, '', '/', 'fleet']) expect(lensPickerFrom(from)).toBe('/board');
  });

  it('never returns to the picker itself', () => {
    expect(lensPickerFrom(`/${LENS_PICKER_PATH}`)).toBe('/board');
    expect(lensPickerFrom(`/${LENS_PICKER_PATH}?from=%2Ffleet`)).toBe('/board');
  });
});
