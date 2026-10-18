import { Injectable, InjectionToken, type Provider, computed, inject } from '@angular/core';
import { QueryClient, injectQuery } from '@tanstack/angular-query-experimental';

/** One server's answer to `GET /api/flags`: every flag its scope resolves, and the names an operator's `[flags]` table
 * overrode. */
export interface FlagsView {
  readonly flags: Readonly<Record<string, boolean>>;
  readonly overridden: readonly string[];
}

/** An app's read of its own server's flags — the query key and the call through that app's generated client. */
export interface FlagsRead {
  readonly queryKey: readonly unknown[];
  readonly queryFn: () => Promise<FlagsView>;
}

/** What a server that could not answer reads as: every flag off, nothing overridden. */
const ALL_OFF: FlagsView = { flags: {}, overridden: [] };

const FLAGS_READ = new InjectionToken<FlagsRead>('fleet.FLAGS_READ');

/** Hands {@link FlagStore} the app's own flags read — the hub board reads the hub, the host panel reads the host. */
export function provideFlags(read: FlagsRead): Provider {
  return { provide: FLAGS_READ, useValue: read };
}

/** The read's options: an answer is final for the page's life — a flag flips on a restart, so nothing refetches it. */
function flagsQueryOptions(read: FlagsRead) {
  return { ...read, staleTime: Infinity, refetchOnWindowFocus: false, refetchOnReconnect: false };
}

/**
 * The one place a feature asks whether a flag is on: `flags.on(name)`, in a template or a `computed`.
 *
 * Reads the app's server once, at start. A name the answer does not hold reads off, and so does every name while the
 * read is pending or has failed — a page never fails on a flag.
 *
 * Hold the injected store as `flags`: the flag registry lint finds a flag read by that
 * receiver name, and fails a web source that holds the store under another.
 */
@Injectable({ providedIn: 'root' })
export class FlagStore {
  private readonly queryClient = inject(QueryClient);
  private readonly options = flagsQueryOptions(inject(FLAGS_READ));
  private readonly query = injectQuery(() => this.options);

  /** The answer, with a part a server left out read as empty — never a throw in a template. */
  private readonly answer = computed<FlagsView>(() => {
    const data = this.query.data();
    return { flags: data?.flags ?? ALL_OFF.flags, overridden: data?.overridden ?? ALL_OFF.overridden };
  });

  /** Whether `name` is on. Signal-backed: a template or `computed` reading it follows the answer. */
  on(name: string): boolean {
    return this.answer().flags[name] === true;
  }

  /** The flags the server's `[flags]` table overrode — empty while unread or failed. */
  readonly overridden = computed(() => this.answer().overridden);

  /** The answer once the read has settled — all off when it failed. What a route guard waits on. */
  async whenSettled(): Promise<void> {
    await this.queryClient.ensureQueryData(this.options).catch(() => ALL_OFF);
  }
}
