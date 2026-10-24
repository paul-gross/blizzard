/** The test's record of everything it created: each id a create call returns, under its kind. One per test. */
export class Ledger {
  private readonly kinds = new Map<string, Set<string>>();

  add(kind: string, id: string): void {
    let ids = this.kinds.get(kind);
    if (ids === undefined) {
      ids = new Set();
      this.kinds.set(kind, ids);
    }
    ids.add(id);
  }

  has(kind: string, id: unknown): boolean {
    return typeof id === 'string' && (this.kinds.get(kind)?.has(id) ?? false);
  }

  /** Whether any kind holds the id: how the event reader decides a frame names something the test made. */
  holds(id: unknown): boolean {
    if (typeof id !== 'string') return false;
    for (const ids of this.kinds.values()) if (ids.has(id)) return true;
    return false;
  }

  ids(kind: string): string[] {
    return [...(this.kinds.get(kind) ?? [])];
  }
}
