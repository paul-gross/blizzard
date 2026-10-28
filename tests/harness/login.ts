import type { APIRequestContext } from '@playwright/test';
import { walk } from './auth';
import type { CliResult, Prompt } from './cli';
import type { HubCoordinates } from './hubs';

/** The one `blizzard hub login --paste` walk: the CLI prints a login URL, the person's context walks it, and the code it shows goes back. */
export async function loginThroughCli(options: {
  run: (args: string[], env: NodeJS.ProcessEnv, prompt?: Prompt) => Promise<CliResult>;
  env: NodeJS.ProcessEnv;
  person: { email: string; context: APIRequestContext };
  hub: Pick<HubCoordinates, 'url' | 'idpUrl' | 'auth'>;
}): Promise<void> {
  const { person } = options;
  const result = await options.run(['hub', 'login', '--paste'], options.env, {
    until: /open this URL to log in, then paste the code it shows: (\S+)/,
    reply: async (match) => {
      const landing = await walk({ context: person.context, hub: options.hub, email: person.email, startUrl: match[1] as string });
      const code = /<pre[^>]*>([^<]+)<\/pre>/.exec(landing.body)?.[1];
      if (landing.status !== 200 || code === undefined) {
        throw new Error(`the login URL for ${person.email} landed at ${landing.status} ${landing.location ?? ''} with no code: ${landing.body.slice(0, 300)}`);
      }
      return code.trim();
    },
  });
  if (result.code !== 0) throw new Error(`\`blizzard hub login --paste\` for ${person.email} exited ${result.code}: ${result.stdout}${result.stderr}`);
}
