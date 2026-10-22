import { openLookup, unlisted } from './open-lookup';

type Light = 'red' | 'green';

describe('openLookup', () => {
  const label = openLookup<Light, string>({ red: 'stop', green: 'go' }, (raw) => `raw ${raw}`);

  it('reads a declared value from its table', () => {
    expect(label('red')).toBe('stop');
    expect(label('green')).toBe('go');
  });

  it('resolves a value outside the vocabulary through the fallback, given the raw value', () => {
    expect(label('amber')).toBe('raw amber');
  });

  it('never reads an inherited property as a declared value', () => {
    expect(label('constructor')).toBe('raw constructor');
    expect(label('toString')).toBe('raw toString');
  });

  it('tells a declared value from one outside the vocabulary', () => {
    expect(label.has('green')).toBe(true);
    expect(label.has('amber')).toBe(false);
    expect(label.has('constructor')).toBe(false);
  });

  it('lists the declared vocabulary in table order', () => {
    expect(label.keys).toEqual(['red', 'green']);
  });
});

describe('unlisted', () => {
  function word(light: Light): string {
    switch (light) {
      case 'red':
        return 'stop';
      case 'green':
        return 'go';
      default:
        return unlisted(light);
    }
  }

  it('hands a switch the raw value no case names', () => {
    expect(word('amber' as Light)).toBe('amber');
  });
});
