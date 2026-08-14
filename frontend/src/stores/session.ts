/**
 * Stores — minimal Zustand stores. Holds cross-page state only.
 * Page-local state stays in the page component.
 *
 * Rules:
 *  - No business logic.
 *  - No API calls inside stores (callers do API then setState).
 *  - Persisted state stays minimal (workspace + UI prefs only).
 *  - currentWorkspaceId is explicit UI state; API callers must pass it through.
 */

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { StateStorage } from "zustand/middleware";
import { scopedLocalStorageKey, setActiveWorkspaceScope } from "../utils/userScope";

const userSessionStorage: StateStorage = {
  getItem: () => localStorage.getItem(scopedLocalStorageKey("lzcore_session", false)),
  setItem: (_name, value) => localStorage.setItem(scopedLocalStorageKey("lzcore_session", false), value),
  removeItem: () => localStorage.removeItem(scopedLocalStorageKey("lzcore_session", false)),
};

export function isInternalSessionId(id: string | null | undefined): boolean {
  const value = (id || "").trim();
  return value.startsWith("sub-") || value.startsWith("internal-");
}

interface SessionState {
  currentWorkspaceId: string;
  currentSessionId: string | null;

  /** Bumped whenever the session list must be re-fetched (e.g. session restored elsewhere). */
  sessionListVersion: number;
  bumpSessionList: () => void;

  setCurrentWorkspace: (id: string) => void;
  setCurrentSession: (id: string | null) => void;
  resetForUser: (workspaceId: string) => void;
  reset: () => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      currentWorkspaceId: "default",
      currentSessionId: null,
      sessionListVersion: 0,
      bumpSessionList: () => set((s) => ({ sessionListVersion: s.sessionListVersion + 1 })),
      setCurrentWorkspace: (id) => {
        setActiveWorkspaceScope(id);
        set({ currentWorkspaceId: id, currentSessionId: null });
      },
      setCurrentSession: (id) => set({ currentSessionId: isInternalSessionId(id) ? null : id }),
      resetForUser: (workspaceId) => set({ currentWorkspaceId: workspaceId, currentSessionId: null }),
      reset: () =>
        set({
          currentSessionId: null,
        }),
    }),
    {
      name: "lzcore_session",
      storage: createJSONStorage(() => userSessionStorage),
      partialize: (s) => ({
        currentWorkspaceId: s.currentWorkspaceId,
        currentSessionId: isInternalSessionId(s.currentSessionId) ? null : s.currentSessionId,
      }),
      merge: (persisted, current) => {
        const p = (persisted || {}) as Partial<SessionState>;
        return {
          ...current,
          ...p,
          currentSessionId: isInternalSessionId(p.currentSessionId) ? null : (p.currentSessionId ?? null),
        };
      },
    },
  ),
);

interface UIState {
  sidebarOpen: boolean;
  /** Off-canvas navigation drawer state for tablet/mobile (≤900px). */
  mobileNavOpen: boolean;
  theme: "light" | "dark";

  toggleSidebar: () => void;
  setMobileNavOpen: (open: boolean) => void;
  toggleMobileNav: () => void;
  setTheme: (t: "light" | "dark") => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set, get) => ({
      sidebarOpen: true,
      mobileNavOpen: false,
      theme: "light",
      toggleSidebar: () => set({ sidebarOpen: !get().sidebarOpen }),
      setMobileNavOpen: (open) => set({ mobileNavOpen: open }),
      toggleMobileNav: () => set({ mobileNavOpen: !get().mobileNavOpen }),
      setTheme: (theme) => set({ theme }),
    }),
    {
      name: "lzcore_ui",
      partialize: (s) => ({
        sidebarOpen: s.sidebarOpen,
        theme: s.theme,
      }),
    },
  ),
);
