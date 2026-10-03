import { drainPages } from './paginated-read';

interface Page {
  rows?: number[];
  next_cursor?: string | null;
}

function fetcherOf(pages: Page[]): (cursor: string | undefined) => Promise<{ data?: Page; error: unknown }> {
  let calls = 0;
  return async () => {
    const page = pages[calls];
    calls += 1;
    return { data: page, error: undefined };
  };
}

describe('drainPages', () => {
  it('concatenates every page in order', async () => {
    const fetchPage = fetcherOf([
      { rows: [1, 2], next_cursor: 'c1' },
      { rows: [3], next_cursor: 'c2' },
      { rows: [4, 5], next_cursor: null },
    ]);

    const rows = await drainPages(fetchPage, (page) => page.rows);

    expect(rows).toEqual([1, 2, 3, 4, 5]);
  });

  it('issues exactly one request when the first page already carries a null next_cursor', async () => {
    let calls = 0;
    const fetchPage = async (): Promise<{ data?: Page; error: unknown }> => {
      calls += 1;
      return { data: { rows: [1], next_cursor: null }, error: undefined };
    };

    const rows = await drainPages(fetchPage, (page) => page.rows);

    expect(rows).toEqual([1]);
    expect(calls).toBe(1);
  });

  it('treats an undefined next_cursor the same as null — the last page', async () => {
    const fetchPage = fetcherOf([{ rows: [1], next_cursor: undefined }]);

    const rows = await drainPages(fetchPage, (page) => page.rows);

    expect(rows).toEqual([1]);
  });

  it('surfaces a mid-drain error as its own rejection, not a partial result', async () => {
    let calls = 0;
    const boom = new Error('boom');
    const fetchPage = async (): Promise<{ data?: Page; error: unknown }> => {
      calls += 1;
      if (calls === 1) return { data: { rows: [1], next_cursor: 'c1' }, error: undefined };
      return { data: undefined, error: boom };
    };

    await expect(drainPages(fetchPage, (page) => page.rows)).rejects.toBe(boom);
  });

  it('treats a page carrying no rows key as empty rather than throwing', async () => {
    const fetchPage = fetcherOf([{ next_cursor: null }]);

    const rows = await drainPages(fetchPage, (page) => page.rows);

    expect(rows).toEqual([]);
  });
});
