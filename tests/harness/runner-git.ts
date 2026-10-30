import * as path from 'node:path';
import * as fs from 'node:fs';
import { runCommand } from './cli';

/** One commit of a bare origin's history. */
export interface OriginCommit {
  sha: string;
  subject: string;
  parents: string[];
}

/** Reads of a minted world's bare origins, by repo name; a ref is a branch name or a commit sha. */
export interface OriginReads {
  /** Every file path in the tree at `ref`, or `undefined` when the repo or ref is not there. */
  originTree(repo: string, ref: string): Promise<string[] | undefined>;
  /** The commits reachable from `ref`, newest first, only those touching `filePath` when given; `undefined` when the repo or ref is not there. */
  originLog(repo: string, ref: string, filePath?: string): Promise<OriginCommit[] | undefined>;
  /** The text of `filePath` at `ref`, or `undefined` when the repo, ref or path is not there. */
  originFile(repo: string, ref: string, filePath: string): Promise<string | undefined>;
  /** Make a test-owned external commit on a branch of the same bare origin the forge merges. */
  originCommit(repo: string, branch: string, filePath: string, text: string, subject: string): Promise<string>;
}

const SHA = /^[0-9a-f]{4,40}$/;

/** Git reads of the bare repos under `origins`, run in the host's isolated `env`. */
export function originReads(origins: string, env: NodeJS.ProcessEnv, cwd: string, bound: Record<string, string> = {}): OriginReads {
  const directory = (repo: string) => bound[repo] ?? path.join(origins, `${repo}.git`);
  const git = (repo: string, args: string[]) => runCommand('git', ['--git-dir', directory(repo), ...args], { env, cwd });

  // A branch of that name first, then a commit sha, so `originHead`'s answer chains in.
  const resolve = async (repo: string, ref: string): Promise<string | undefined> => {
    const candidates = [`refs/heads/${ref}`, ...(SHA.test(ref) ? [ref] : [])];
    for (const candidate of candidates) {
      const result = await git(repo, ['rev-parse', '--verify', '--quiet', `${candidate}^{commit}`]);
      if (result.code === 0) return result.stdout.trim();
    }
    return undefined;
  };

  return {
    async originTree(repo, ref) {
      const sha = await resolve(repo, ref);
      if (sha === undefined) return undefined;
      const result = await git(repo, ['ls-tree', '-r', '--name-only', sha]);
      return result.code === 0 ? result.stdout.split('\n').filter((line) => line !== '') : undefined;
    },
    async originLog(repo, ref, filePath) {
      const sha = await resolve(repo, ref);
      if (sha === undefined) return undefined;
      const result = await git(repo, ['log', '--format=%H%x00%P%x00%s', sha, ...(filePath === undefined ? [] : ['--', filePath])]);
      if (result.code !== 0) return undefined;
      return result.stdout
        .split('\n')
        .filter((line) => line !== '')
        .map((line) => {
          const [commit = '', parents = '', subject = ''] = line.split('\0');
          return { sha: commit, parents: parents === '' ? [] : parents.split(' '), subject };
        });
    },
    async originFile(repo, ref, filePath) {
      const sha = await resolve(repo, ref);
      if (sha === undefined) return undefined;
      const result = await git(repo, ['cat-file', 'blob', `${sha}:${filePath}`]);
      return result.code === 0 ? result.stdout : undefined;
    },
    async originCommit(repo, branch, filePath, text, subject) {
      if (!bound[repo]) throw new Error(`external commits require a test-owned forge repository: ${repo}`);
      if (filePath.startsWith('/') || filePath.split('/').includes('..')) throw new Error(`invalid repository path: ${filePath}`);
      const parent = await resolve(repo, branch);
      if (parent === undefined) throw new Error(`missing branch ${branch} in ${repo}`);
      const scratch = fs.mkdtempSync(path.join(cwd, 'external-'));
      try {
        const work = path.join(scratch, 'work');
        const clone = await runCommand('git', ['clone', directory(repo), work], { env, cwd });
        if (clone.code !== 0) throw new Error(clone.stderr);
        const run = async (args: string[]) => {
          const result = await runCommand('git', ['-C', work, ...args], { env, cwd });
          if (result.code !== 0) throw new Error(`git ${args.join(' ')}: ${result.stderr}`);
          return result.stdout.trim();
        };
        await run(['checkout', '-B', branch, parent]);
        const target = path.join(work, filePath);
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, text);
        await run(['add', '--', filePath]);
        await run(['-c', 'user.name=External Test', '-c', 'user.email=external@example.invalid', 'commit', '-m', subject]);
        const sha = await run(['rev-parse', 'HEAD']);
        await run(['push', 'origin', `HEAD:refs/heads/${branch}`]);
        return sha;
      } finally {
        fs.rmSync(scratch, { recursive: true, force: true });
      }
    },
  };
}
