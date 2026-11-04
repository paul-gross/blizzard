import { ClockRouteMissing, type ClockClient } from './client/clock';

/** The window a hub session slides by on each touch; `src/blizzard/hub/auth/service.py::IDLE_TTL`, 24 hours. */
export const IDLE_TTL_MS = 24 * 60 * 60 * 1000;

/** The mode a test's stack runs in: `'system'` reads the machine's time, `'controlled'` runs every daemon on a controlled clock. */
export type ClockMode = 'system' | 'controlled';

/** The `BLIZZARD_CLOCK` value a controlled daemon is started with. */
export const CONTROLLED_VALUE = 'controlled';

/** The environment a daemon of a controlled stack is started with, and nothing on a system stack. */
export function clockEnv(controlled: boolean): Record<string, string> {
  return controlled ? { BLIZZARD_CLOCK: CONTROLLED_VALUE } : {};
}

/** The order members move in: the hub or mock hub, then runner hosts, then mock runners and forges, then the page. */
export const RANK = { hub: 0, host: 1, daemon: 2, page: 3 } as const;

/** One clock in a controlled stack. */
export interface ClockMember {
  /** Names the member in a failure. */
  name: string;
  rank: number;
  /** What the member's clock reads; a member with none (the page) takes the stack's instant on joining. */
  client: Pick<ClockClient, 'set'> & Partial<Pick<ClockClient, 'read'>>;
  /** Told the member's own first reading when it joins, before alignment. */
  joined?: (reading: Date) => void;
}

/** A member's place in the stack, until it leaves. */
export interface Membership {
  leave(): void;
}

/** The spec-facing clock: every spec reads time through it, whatever the stack's mode. */
export interface Clock {
  /** A controlled stack's instant as the fixture last set it, its own record and not a daemon read; the machine's time otherwise. */
  now(): Date;
  /** Moves every clock of a controlled stack, the browser's last. Refused on a system stack, naming `clockMode`. */
  advance(seconds: number): Promise<Date>;
  /** The same, to an instant, which may not lie before `now()`. */
  set(instant: Date): Promise<Date>;
  /** Skips refreshing sessions before the next request each signed-in client sends. Has no effect on a system stack. */
  suspendSessionRefresh(): void;
}

/** A signed-in credential the harness keeps alive past the hub's idle window. */
export interface SessionHolder {
  /** The hub's own reading when the session was signed in; set by the holder on a sign-in. */
  signedInAt: Date | undefined;
  /** Signs in again, setting `signedInAt`. */
  resign(): Promise<void>;
}

/**
 * The record behind the `clock` fixture: its mode, the instant, the members whose clocks it moves, and the sessions it
 * keeps alive. A system stack holds nothing and moves nothing.
 */
export class ClockStack implements Clock {
  private instant: Date | undefined;
  private readonly members: ClockMember[] = [];
  private readonly holders = new Set<SessionHolder>();
  private suspended = new Set<SessionHolder>();
  private queue: Promise<unknown> = Promise.resolve();

  constructor(readonly mode: ClockMode) {}

  get controlled(): boolean {
    return this.mode === 'controlled';
  }

  now(): Date {
    if (!this.controlled) return new Date();
    this.instant ??= new Date();
    return new Date(this.instant.getTime());
  }

  async advance(seconds: number): Promise<Date> {
    this.requireControlled('advance');
    if (!Number.isFinite(seconds) || seconds < 0) throw new Error(`clock.advance(${seconds}): a clock only moves forward, by a finite number of seconds`);
    return this.set(new Date(this.now().getTime() + Math.round(seconds * 1000)));
  }

  async set(instant: Date): Promise<Date> {
    this.requireControlled('set');
    // Refused before any request, so a refused move never half-moves the stack.
    if (instant.getTime() < this.now().getTime()) {
      throw new Error(`clock.set(${instant.toISOString()}) lies before the stack's instant ${this.now().toISOString()}; a controlled clock only moves forward`);
    }
    return this.exclusive(() => this.moveTo(instant));
  }

  suspendSessionRefresh(): void {
    if (!this.controlled) return;
    this.suspended = new Set(this.holders);
  }

  private requireControlled(what: string): void {
    if (!this.controlled) throw new Error(`clock.${what}() moves a controlled stack, and this one runs on the system clock. Declare test.use({ clockMode: 'controlled' }).`);
  }

  /** Runs `step` after every operation begun before it, so joins and moves of one stack never interleave. */
  private exclusive<T>(step: () => Promise<T>): Promise<T> {
    const run = this.queue.then(step, step);
    this.queue = run.catch(() => undefined);
    return run;
  }

  private async moveTo(to: Date, except?: ClockMember): Promise<Date> {
    for (const member of [...this.members].sort((a, b) => a.rank - b.rank)) {
      if (member !== except) await member.client.set(to);
    }
    this.instant = to;
    return new Date(to.getTime());
  }

  /**
   * Aligns a daemon's clock with the stack's before it resolves. Behind the stack, the member is set forward to
   * `now()`; ahead of it, the whole stack moves forward to the member's reading. A system stack joins nothing.
   */
  join(member: ClockMember): Promise<Membership> {
    if (!this.controlled) return Promise.resolve({ leave: () => undefined });
    return this.exclusive(async () => {
      let reading: Date | undefined;
      try {
        reading = await member.client.read?.();
      } catch (error) {
        if (error instanceof ClockRouteMissing) {
          throw new Error(`${member.name} did not take the controlled clock: ${error.message}. Its BLIZZARD_CLOCK_FENCE or BLIZZARD_CLOCK did not arrive; check BLIZZARD_CLOCK_FENCE in tests/daemon-env.json.`, { cause: error });
        }
        throw error;
      }
      if (reading !== undefined) member.joined?.(reading);
      this.instant ??= reading ?? new Date();
      if (reading !== undefined && reading.getTime() > this.instant.getTime()) {
        // The member is the one ahead: it stays where it is, and everything else comes forward to it.
        await this.moveTo(reading, member);
      } else {
        await member.client.set(this.instant);
      }
      this.members.push(member);
      return {
        leave: () => {
          const at = this.members.indexOf(member);
          if (at >= 0) this.members.splice(at, 1);
        },
      };
    });
  }

  /**
   * The reading of the stack's hub (or mock hub), the instant a session's sign-in is stamped with. `undefined` before a hub
   * joined: a session made then is stamped with the reading the join reads.
   */
  async hubReading(): Promise<Date | undefined> {
    return this.members.find((member) => member.rank === RANK.hub)?.client.read?.();
  }

  /** Registers a signed-in credential, so the harness re-signs it before the next request once the hub's window has passed. */
  register(holder: SessionHolder): { refresh(options?: { force?: boolean }): Promise<void>; release(): void } {
    if (this.controlled) this.holders.add(holder);
    // The stamp a re-sign takes reads the hub, which reads the session: a refresh under way is not asked again.
    let signing = false;
    return {
      // `force` is the harness's own use, which suspension never skips.
      refresh: async (options = {}) => {
        if (!this.controlled || signing) return;
        if (options.force !== true && this.suspended.delete(holder)) return;
        const signed = holder.signedInAt;
        if (signed === undefined || this.now().getTime() < signed.getTime() + IDLE_TTL_MS) return;
        signing = true;
        try {
          await holder.resign();
        } finally {
          signing = false;
        }
      },
      release: () => {
        this.holders.delete(holder);
        this.suspended.delete(holder);
      },
    };
  }
}
