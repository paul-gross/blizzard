import { entityDisplay } from './compact-ref';

describe('entityDisplay', () => {
  it('maps colliding wire prefixes to distinct sigils', () => {
    expect(entityDisplay('ch')).toEqual({ sigil: 'C', tailLength: 4 });
    expect(entityDisplay('cho')).toEqual({ sigil: 'CH', tailLength: 4 });
    expect(entityDisplay('fins')).toEqual({ sigil: 'FS', tailLength: 4 });
  });

  it('uses the default for unknown and prototype-named prefixes', () => {
    expect(entityDisplay('future')).toEqual({ sigil: 'F', tailLength: 4 });
    expect(entityDisplay('constructor')).toEqual({ sigil: 'C', tailLength: 4 });
  });
});
