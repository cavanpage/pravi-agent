import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../lib/api";
import { CloudflareConnectModal } from "./CloudflareConnectModal";

/**
 * Header chip: "Connect Cloudflare" when disconnected, `acme-inc` account
 * name when connected. Clicking the connected chip drops a small menu with
 * "disconnect".
 *
 * Mirrors the GitHubConnectButton pattern, but the connect path can't be
 * OAuth (Cloudflare has no third-party OAuth program) — we open the
 * existing token-paste modal instead. Once the modal succeeds it
 * invalidates the ["cloudflareMe"] query so this chip flips state.
 */
export function CloudflareConnectButton() {
  const qc = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);

  const q = useQuery({
    queryKey: ["cloudflareMe"],
    queryFn: () => api.cloudflareMe(),
    refetchOnWindowFocus: true,
  });

  const logoutMut = useMutation({
    mutationFn: () => api.cloudflareDisconnect(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cloudflareMe"] });
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });

  if (q.isLoading) {
    return (
      <span className="px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-xs text-neutral-500">
        cloudflare…
      </span>
    );
  }

  const conn = q.data;

  if (!conn) {
    return (
      <>
        <button
          onClick={() => setModalOpen(true)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm font-medium text-neutral-200 transition"
          title="Paste a Cloudflare API token so pravi can create Pages projects for you"
        >
          <CloudflareGlyph />
          Connect Cloudflare
        </button>
        {modalOpen ? (
          <CloudflareConnectModal
            onClose={() => setModalOpen(false)}
            onConnected={() => {
              setModalOpen(false);
              qc.invalidateQueries({ queryKey: ["cloudflareMe"] });
            }}
          />
        ) : null}
      </>
    );
  }

  const label = conn.account_name || conn.account_id.slice(0, 8);
  return (
    <details className="relative group">
      <summary className="cursor-pointer list-none inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/5 hover:bg-white/10 border border-emerald-400/30 text-sm font-medium text-neutral-200 transition">
        <CloudflareGlyph />
        <span>{label}</span>
        <span className="text-neutral-500 text-xs">▾</span>
      </summary>
      <div className="absolute right-0 mt-1 min-w-[220px] rounded-xl border border-white/10 bg-neutral-900 shadow-xl shadow-black/40 p-2 z-10">
        <div className="text-[11px] text-neutral-500 px-2 py-1 font-mono truncate">
          account: {conn.account_id}
        </div>
        {conn.token_id ? (
          <div className="text-[11px] text-neutral-500 px-2 py-1 font-mono truncate">
            token: {conn.token_id}
          </div>
        ) : null}
        <button
          onClick={() => logoutMut.mutate()}
          disabled={logoutMut.isPending}
          className="w-full text-left px-2 py-1.5 rounded-lg text-sm text-rose-300 hover:bg-rose-400/10 transition disabled:opacity-40"
        >
          {logoutMut.isPending ? "disconnecting…" : "disconnect"}
        </button>
      </div>
    </details>
  );
}

// Cloudflare's official cloud glyph, single-path so it inherits `currentColor`.
function CloudflareGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-3.5"
      fill="currentColor"
      aria-hidden
    >
      <path d="M16.5 16.5H5.25a3.75 3.75 0 0 1-.4-7.48 5.25 5.25 0 0 1 10.14-1.4A4.5 4.5 0 0 1 22.5 12a4.5 4.5 0 0 1-4.5 4.5h-1.5Z" />
    </svg>
  );
}
