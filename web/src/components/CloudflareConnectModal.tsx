import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  api,
  CloudflareAccountOption,
  CloudflareAccountPickerError,
  CloudflareConnection,
} from "../lib/api";

/** "Connect Cloudflare" modal — paste-token onboarding so the user
 * doesn't have to edit `.env` and restart the server.
 *
 * Cloudflare doesn't expose a self-serve third-party OAuth program,
 * so the friction this modal removes is: looking up the account id,
 * knowing which permissions to tick, and surviving the restart cycle.
 *
 * Flow:
 *   1. User clicks the "create token" deep link → Cloudflare dashboard.
 *   2. They paste the token here + submit.
 *   3. Server probes `/user/tokens/verify` and `/accounts`.
 *      - 1 account → auto-pick.
 *      - multiple → server returns 409 with the account list; modal
 *        swaps to an account picker; user picks; re-submits.
 *   4. Persist → `integrations` query refetches → the Cloudflare toggle
 *      in CreateRepoModal lights up. */

const TOKEN_CREATE_URL = "https://dash.cloudflare.com/profile/api-tokens";

export function CloudflareConnectModal({
  onClose,
  onConnected,
}: {
  onClose: () => void;
  onConnected: (conn: CloudflareConnection) => void;
}) {
  const qc = useQueryClient();

  const [token, setToken] = useState("");
  const [accountChoices, setAccountChoices] = useState<
    CloudflareAccountOption[] | null
  >(null);
  const [pickedAccountId, setPickedAccountId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const connectMut = useMutation({
    mutationFn: (vars: { api_token: string; account_id?: string }) =>
      api.cloudflareConnect(vars),
    onSuccess: (conn) => {
      qc.invalidateQueries({ queryKey: ["integrations"] });
      qc.invalidateQueries({ queryKey: ["cloudflareMe"] });
      onConnected(conn);
    },
    onError: (e: Error) => {
      if (e instanceof CloudflareAccountPickerError) {
        // Token is good but maps to multiple accounts → render picker.
        setAccountChoices(e.accounts);
        setPickedAccountId(e.accounts[0]?.id ?? null);
        setError(null);
        return;
      }
      setError(e.message);
    },
  });

  const submitting = connectMut.isPending;
  const trimmedToken = token.trim();

  function submit() {
    setError(null);
    if (!trimmedToken) {
      setError("Paste your Cloudflare API token first.");
      return;
    }
    connectMut.mutate({
      api_token: trimmedToken,
      account_id: pickedAccountId ?? undefined,
    });
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-2xl border border-white/10 bg-neutral-950 p-6 flex flex-col gap-4 max-h-[85vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Connect Cloudflare</h2>
            <p className="text-xs text-neutral-500 mt-1 leading-relaxed">
              Paste an API token so pravi can create Cloudflare Pages
              projects on your behalf. No OAuth, no <span className="font-mono">.env</span> edit,
              no restart.
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

        {accountChoices === null ? (
          <TokenStep
            token={token}
            setToken={setToken}
            error={error}
            submitting={submitting}
          />
        ) : (
          <AccountPickerStep
            accounts={accountChoices}
            pickedAccountId={pickedAccountId}
            setPickedAccountId={setPickedAccountId}
            error={error}
          />
        )}

        <footer className="flex items-center gap-3 flex-wrap mt-2">
          <button
            type="button"
            onClick={submit}
            disabled={
              submitting ||
              !trimmedToken ||
              (accountChoices !== null && !pickedAccountId)
            }
            className="px-4 py-2 rounded-full bg-emerald-500 hover:bg-emerald-400 text-white text-sm font-medium shadow-lg shadow-emerald-500/25 disabled:opacity-40 transition"
          >
            {submitting
              ? "verifying…"
              : accountChoices === null
                ? "connect"
                : "use this account"}
          </button>
          {accountChoices !== null ? (
            <button
              type="button"
              onClick={() => {
                setAccountChoices(null);
                setPickedAccountId(null);
              }}
              className="px-3 py-2 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm transition"
            >
              ← change token
            </button>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            className="ml-auto px-3 py-2 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm transition"
          >
            cancel
          </button>
        </footer>
      </div>
    </div>
  );
}

function TokenStep({
  token,
  setToken,
  error,
  submitting,
}: {
  token: string;
  setToken: (v: string) => void;
  error: string | null;
  submitting: boolean;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3.5 flex flex-col gap-2">
        <div className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400">
          1 · Create the token
        </div>
        <p className="text-xs text-neutral-400 leading-relaxed">
          Open Cloudflare's API token page → <em>Create Token</em> →{" "}
          <em>Custom token</em>. Add these permissions and click <em>Create</em>:
        </p>
        <ul className="text-xs text-neutral-300 font-mono leading-relaxed pl-4 list-disc">
          <li>
            <span className="text-emerald-300">Account</span> · Cloudflare Pages
            · Edit
          </li>
          <li>
            <span className="text-emerald-300">Account</span> · Account Settings
            · Read
          </li>
        </ul>
        <a
          href={TOKEN_CREATE_URL}
          target="_blank"
          rel="noreferrer"
          className="self-start inline-flex items-center gap-1 text-sm px-3 py-1.5 rounded-full bg-blue-500/15 hover:bg-blue-500/25 text-blue-200 border border-blue-400/30 transition"
        >
          open token page →
        </a>
      </div>

      <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3.5 flex flex-col gap-2">
        <div className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400">
          2 · Paste it here
        </div>
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="cf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
          autoFocus
          disabled={submitting}
          className="w-full px-3.5 py-2.5 rounded-xl bg-white/[0.03] border border-white/10 text-sm text-neutral-100 placeholder-neutral-600 focus:outline-none focus:border-emerald-400/40 disabled:opacity-60 transition font-mono"
        />
        <p className="text-[11px] text-neutral-500 leading-relaxed">
          Pravi probes the token, auto-detects your account, and stores it
          in its database. The token never leaves this machine — it's used
          server-side to call the Cloudflare API.
        </p>
      </div>

      {error ? (
        <div className="rounded-xl border border-rose-400/25 bg-rose-400/[0.06] text-rose-300 px-3 py-2 text-sm">
          {error}
        </div>
      ) : null}
    </div>
  );
}

function AccountPickerStep({
  accounts,
  pickedAccountId,
  setPickedAccountId,
  error,
}: {
  accounts: CloudflareAccountOption[];
  pickedAccountId: string | null;
  setPickedAccountId: (id: string) => void;
  error: string | null;
}) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-neutral-400 leading-relaxed">
        This token can access multiple Cloudflare accounts. Pick the one
        you want pravi to deploy Pages projects under.
      </p>
      <ul className="rounded-xl border border-white/10 bg-white/[0.02] divide-y divide-white/5">
        {accounts.map((a) => {
          const picked = a.id === pickedAccountId;
          return (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => setPickedAccountId(a.id)}
                className={`w-full text-left px-3.5 py-2.5 flex items-center gap-3 transition ${
                  picked ? "bg-emerald-400/[0.08]" : "hover:bg-white/[0.04]"
                }`}
              >
                <span
                  className={`inline-flex size-4 rounded-full border ${
                    picked
                      ? "border-emerald-400 bg-emerald-400"
                      : "border-white/30"
                  }`}
                  aria-hidden="true"
                />
                <span className="flex-1 min-w-0">
                  <span className="block text-sm text-neutral-100 truncate">
                    {a.name}
                  </span>
                  <span className="block text-[11px] text-neutral-500 font-mono truncate">
                    {a.id}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {error ? (
        <div className="rounded-xl border border-rose-400/25 bg-rose-400/[0.06] text-rose-300 px-3 py-2 text-sm">
          {error}
        </div>
      ) : null}
    </div>
  );
}
