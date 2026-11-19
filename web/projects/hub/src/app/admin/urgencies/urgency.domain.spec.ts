import { urgencyMarkError, URGENCY_ICON_SIZE_ERROR } from './urgency.domain';

describe('urgency mark validation', () => {
  it('accepts slash-prefixed marks admitted by the hub', () => {
    for (const mark of ['/', '/🔥', ' /🔥 ']) expect(urgencyMarkError(mark)).toBeNull();
  });

  it('retains emoji and image validation', () => {
    const png = '\x89PNG\r\n\x1a\n';
    const image = (bytes: string) => `data:image/png;base64,${btoa(bytes)}`;
    expect(urgencyMarkError(image(png))).toBeNull();
    expect(urgencyMarkError(image(png + 'x'.repeat(32769 - png.length)))).toBe(URGENCY_ICON_SIZE_ERROR);
    for (const mark of ['data:image/png;base64,%%%', image('bad'), 'data:image/svg+xml;base64,PHN2Zz4=', '/abc', '/1']) {
      expect(urgencyMarkError(mark)).not.toBeNull();
    }
  });
});
