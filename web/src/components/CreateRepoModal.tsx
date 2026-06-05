import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, CreateRepoResult, GitHubRepoResult } from "../lib/api";
import { CloudflareConnectModal } from "./CloudflareConnectModal";

/** Modal for the "create new repo + template + (optional) Cloudflare
 * Pages auto-deploy" flow. Triggered from the GitHubRepoPicker on /new.
 *
 * Lifecycle:
 *   1. User fills in name + (optional) description + visibility +
 *      template + Pages toggle.
 *   2. Submit → POST to /api/auth/github/repos/new.
 *   3. Result screen shows: ✓ repo on GitHub, ✓ initial commit, ✓ Pages
 *      project (with live URL), partial-success / errors if any.
 *   4. "Use this repo" → calls `onPicked(repo)` and the parent picker
 *      treats the new repo as if it had been picked from search. */

export function CreateRepoModal({
  defaultPrivate = true,
  onClose,
  onPicked,
}: {
  defaultPrivate?: boolean;
  onClose: () => void;
  onPicked: (repo: GitHubRepoResult) => void;
}) {
  const qc = useQueryClient();

  // Form state.
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isPrivate, setIsPrivate] = useState(defaultPrivate);
  const [deployToPages, setDeployToPages] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCfConnect, setShowCfConnect] = useState(false);

  const integrationsQ = useQuery({
    queryKey: ["integrations"],
    queryFn: () => api.integrations(),
    staleTime: 60_000,
  });
  const cfConfigured = integrationsQ.data?.cloudflare.configured ?? false;

  // Live-slugged name for the Pages subdomain preview.
  const slug = useMemo(() => slugify(name), [name]);

  const createMut = useMutation({
    mutationFn: () =>
      api.createNewRepo({
        name: slug,
        description,
        private: isPrivate,
        template: "vite-react-static",
        deploy_to_cloudflare_pages: deployToPages && cfConfigured,
        register_in_pravi: true,
      }),
    onSuccess: () => {
      // Refresh the GitHub repo search so the new repo shows up in the
      // picker behind the modal.
      qc.invalidateQueries({ queryKey: ["githubRepos"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  // Auto-dismiss disabled — we want the result screen to be sticky so
  // the user sees the Pages URL.

  const result = createMut.data ?? null;
  const submitting = createMut.isPending;
  const canSubmit = slug.length > 0 && !submitting;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl rounded-2xl border border-white/10 bg-neutral-950 p-6 flex flex-col gap-4 max-h-[85vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">
              {result ? "Repo created" : "Create new repo"}
            </h2>
            <p className="text-xs text-neutral-500 mt-1">
              {result
                ? "Scaffolded from a template. Use it right away — start an epic against it below."
                : "Scaffolds a Vite + React + Tailwind starter and (optionally) wires it up to Cloudflare Pages for auto-deploy on every push."}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-neutral-500 hover:text-neutral-200 text-lg leading-none px-2"
            aria-label="close"
          >
            ×
          </button>
        </header>

        {result ? (
          <ResultPanel result={result} />
        ) : (
          <FormPanel
            name={name}
            setName={setName}
            slug={slug}
            description={description}
            setDescription={setDescription}
            isPrivate={isPrivate}
            setIsPrivate={setIsPrivate}
            deployToPages={deployToPages}
            setDeployToPages={setDeployToPages}
            cfConfigured={cfConfigured}
            cfLoading={integrationsQ.isLoading}
            onConnectCloudflare={() => setShowCfConnect(true)}
          />
        )}

        {error && !result ? (
          <div className="rounded-xl border border-rose-400/25 bg-rose-400/[0.06] text-rose-300 px-3 py-2 text-sm">
            {error}
          </div>
        ) : null}

        <footer className="flex items-center gap-3 flex-wrap mt-2">
          {result ? (
            <button
              type="button"
              onClick={() => onPicked(result.repo)}
              className="px-4 py-2 rounded-full bg-blue-500 hover:bg-blue-400 text-white text-sm font-medium shadow-lg shadow-blue-500/25 transition"
            >
              use this repo →
            </button>
          ) : (
            <button
              type="button"
              onClick={() => createMut.mutate()}
              disabled={!canSubmit}
              className="px-4 py-2 rounded-full bg-emerald-500 hover:bg-emerald-400 text-white text-sm font-medium shadow-lg shadow-emerald-500/25 disabled:opacity-40 transition"
            >
              {submitting ? "creating…" : "create repo"}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm transition"
          >
            {result ? "close" : "cancel"}
          </button>
        </footer>
      </div>

      {showCfConnect ? (
        <CloudflareConnectModal
          onClose={() => setShowCfConnect(false)}
          onConnected={() => {
            setShowCfConnect(false);
            // Make sure the user notices the toggle just lit up.
            setDeployToPages(true);
          }}
        />
      ) : null}
    </div>
  );
}

function FormPanel(props: {
  name: string;
  setName: (v: string) => void;
  slug: string;
  description: string;
  setDescription: (v: string) => void;
  isPrivate: boolean;
  setIsPrivate: (v: boolean) => void;
  deployToPages: boolean;
  setDeployToPages: (v: boolean) => void;
  cfConfigured: boolean;
  cfLoading: boolean;
  onConnectCloudflare: () => void;
}) {
  const {
    name, setName, slug,
    description, setDescription,
    isPrivate, setIsPrivate,
    deployToPages, setDeployToPages,
    cfConfigured, cfLoading,
    onConnectCloudflare,
  } = props;

  return (
    <div className="flex flex-col gap-4">
      <Field label="Name" hint="Lowercase letters / numbers / hyphens. Doubles as the Cloudflare Pages subdomain.">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-app"
          autoFocus
          className="w-full px-3.5 py-2.5 rounded-xl bg-white/[0.03] border border-white/10 text-sm text-neutral-100 focus:outline-none focus:border-emerald-400/40 transition"
        />
        {slug && slug !== name ? (
          <div className="mt-1.5 text-[11px] text-neutral-500 font-mono">
            will be created as <span className="text-neutral-300">{slug}</span>
          </div>
        ) : null}
      </Field>

      <Field label="Description" hint="Optional. Shows on the GitHub repo page.">
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What is this?"
          className="w-full px-3.5 py-2.5 rounded-xl bg-white/[0.03] border border-white/10 text-sm text-neutral-100 focus:outline-none focus:border-emerald-400/40 transition"
        />
      </Field>

      <Field label="Visibility">
        <div className="inline-flex rounded-full border border-white/10 bg-white/[0.02] p-0.5">
          <ToggleChip active={isPrivate} onClick={() => setIsPrivate(true)}>
            private
          </ToggleChip>
          <ToggleChip active={!isPrivate} onClick={() => setIsPrivate(false)}>
            public
          </ToggleChip>
        </div>
      </Field>

      <Field label="Template">
        <div className="rounded-xl border border-white/10 bg-white/[0.02] px-3.5 py-2.5">
          <div className="text-sm text-neutral-100">Vite + React + Tailwind</div>
          <div className="text-[11px] text-neutral-500 mt-0.5">
            TypeScript starter — builds to <span className="font-mono">dist/</span>, deployable anywhere static.
          </div>
        </div>
      </Field>

      <Field
        label="Cloudflare Pages"
        hint={
          cfLoading
            ? "checking integration status…"
            : cfConfigured
              ? "Connects the repo to a Pages project — auto-deploys every push to main. Your site lands at https://<name>.pages.dev."
              : undefined
        }
      >
        {cfConfigured ? (
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={deployToPages}
              onChange={(e) => setDeployToPages(e.target.checked)}
              className="size-4 accent-emerald-400"
            />
            <span className="text-sm text-neutral-100">
              Deploy to Cloudflare Pages
            </span>
            {deployToPages && slug ? (
              <span className="text-[11px] text-neutral-500 font-mono ml-1">
                {"→ "}{slug}.pages.dev
              </span>
            ) : null}
          </label>
        ) : (
          <div className="rounded-xl border border-white/10 bg-white/[0.02] px-3.5 py-3 flex items-center gap-3 flex-wrap">
            <div className="flex-1 min-w-0">
              <div className="text-sm text-neutral-200">
                Not connected yet
              </div>
              <div className="text-[11px] text-neutral-500 mt-0.5 leading-relaxed">
                One-click setup — paste an API token, pick an account, done.
                No <span className="font-mono">.env</span> editing, no restart.
              </div>
            </div>
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onConnectCloudflare();
              }}
              disabled={cfLoading}
              className="px-3 py-1.5 rounded-full bg-orange-500 hover:bg-orange-400 text-white text-xs font-medium shadow-lg shadow-orange-500/20 disabled:opacity-40 transition shrink-0"
            >
              connect cloudflare →
            </button>
          </div>
        )}
      </Field>

      <div className="text-[11px] text-neutral-500 leading-relaxed">
        <strong className="text-neutral-400">One-time setup</strong> if you've
        never used Pages: in the Cloudflare dashboard go to{" "}
        <span className="font-mono">Workers &amp; Pages → Connect to Git</span>{" "}
        and authorize the Cloudflare GitHub app on your account. Without
        that the project links but pushes won't auto-deploy.
      </div>
    </div>
  );
}

function ResultPanel({ result }: { result: CreateRepoResult }) {
  const repo = result.repo;
  return (
    <div className="flex flex-col gap-3">
      <ResultRow ok={true}>
        <a
          href={`https://github.com/${repo.full_name}`}
          target="_blank"
          rel="noreferrer"
          className="text-blue-300 hover:underline font-mono"
        >
          {repo.full_name}
        </a>{" "}
        <span className="text-neutral-500">created on GitHub</span>
      </ResultRow>
      <ResultRow ok={result.initial_commit_pushed}>
        {result.initial_commit_pushed
          ? "initial commit pushed (template + .builder/domains.yaml)"
          : "initial commit failed — push template manually"}
      </ResultRow>
      {result.pages ? (
        <ResultRow ok={true}>
          <span className="text-neutral-500">Cloudflare Pages:</span>{" "}
          <a
            href={result.pages.pages_url}
            target="_blank"
            rel="noreferrer"
            className="text-emerald-300 hover:underline font-mono"
          >
            {result.pages.pages_url}
          </a>
          <div className="text-[11px] text-neutral-500 mt-0.5">
            First build kicks off in ~30s. Every subsequent push to main
            redeploys.
          </div>
        </ResultRow>
      ) : result.pages_skipped_reason ? (
        <ResultRow ok={null}>
          <span className="text-amber-300">Cloudflare Pages skipped:</span>{" "}
          <span className="text-neutral-400">{result.pages_skipped_reason}</span>
        </ResultRow>
      ) : null}
      {result.pravi_repo_id ? (
        <ResultRow ok={true}>
          registered in pravi — ready to use as a ticket target
        </ResultRow>
      ) : null}
    </div>
  );
}

function ResultRow({
  ok,
  children,
}: {
  /** true = green check, false = red x, null = neutral info */
  ok: boolean | null;
  children: React.ReactNode;
}) {
  const icon = ok === true ? "✓" : ok === false ? "✕" : "•";
  const tone =
    ok === true
      ? "text-emerald-300"
      : ok === false
        ? "text-rose-300"
        : "text-neutral-500";
  return (
    <div className="flex items-start gap-2 text-sm">
      <span className={`shrink-0 w-4 ${tone}`}>{icon}</span>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400 mb-1.5">
        {label}
      </span>
      {children}
      {hint ? (
        <span className="block text-xs text-neutral-500 mt-1.5 leading-relaxed">
          {hint}
        </span>
      ) : null}
    </label>
  );
}

function ToggleChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1 text-xs rounded-full transition ${
        active
          ? "bg-white/10 text-neutral-100"
          : "text-neutral-500 hover:text-neutral-200"
      }`}
    >
      {children}
    </button>
  );
}

/** Lower-case, replace non-alphanumeric with hyphens, collapse runs,
 * trim leading/trailing hyphens. Matches GitHub's accepted repo-name
 * shape, and the Cloudflare Pages subdomain rules. */
function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63);
}
