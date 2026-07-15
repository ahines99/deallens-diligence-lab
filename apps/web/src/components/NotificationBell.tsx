"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useActor } from "@/components/identity/ActorContext";
import { api, ApiError } from "@/lib/api";
import type { NotificationItem } from "@/lib/types";

const POLL_MS = 30_000;

/** Header notification center (G33): polls the org's audit-fed notifications. */
export function NotificationBell() {
  const { actor } = useActor();
  const organizationId = actor.organizationId;
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    if (!organizationId) return;
    try {
      const [list, count] = await Promise.all([
        api.listNotifications(organizationId),
        api.notificationsUnreadCount(organizationId),
      ]);
      setItems(list.slice(0, 20));
      setUnread(count.unread);
    } catch (e) {
      // A notification outage is never surfaced as "0 unread"; keep the last known state.
      if (e instanceof ApiError && e.status === 0) return;
    }
  }, [organizationId]);

  useEffect(() => {
    void refresh();
    if (!organizationId) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [organizationId, refresh]);

  useEffect(() => {
    function close(event: MouseEvent) {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  async function markRead(id: string) {
    try {
      await api.markNotificationRead(id);
      setItems((prev) => prev.map((n) => (n.id === id ? { ...n, read_at: new Date().toISOString() } : n)));
      setUnread((u) => Math.max(0, u - 1));
    } catch {
      /* best-effort */
    }
  }

  if (!organizationId) return null;

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative rounded-md px-2 py-1 text-sm text-white/80 hover:bg-white/10"
        aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
      >
        <span aria-hidden>🔔</span>
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-gold px-1 text-2xs font-semibold text-ink">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-80 overflow-hidden rounded-md border border-line bg-panel text-ink shadow-sm">
          <div className="border-b border-line-faint px-3 py-2 text-xs font-semibold text-muted">
            Notifications
          </div>
          {items.length === 0 ? (
            <div className="px-3 py-4 text-sm text-muted">No notifications yet.</div>
          ) : (
            <ul className="max-h-96 overflow-y-auto">
              {items.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    onClick={() => n.read_at || markRead(n.id)}
                    className={`block w-full px-3 py-2.5 text-left hover:bg-panel2 ${n.read_at ? "opacity-60" : ""}`}
                  >
                    <div className="flex items-center gap-2">
                      {!n.read_at && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden />}
                      <span className="text-sm font-semibold text-ink">{n.title}</span>
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-muted">{n.body}</p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default NotificationBell;
