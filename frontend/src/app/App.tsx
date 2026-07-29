import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Suspense, memo, useCallback, useEffect, useState } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { SkeletonList, SkeletonTable } from "../components/common";
import { AppLayout } from "../layouts/AppLayout";
import { ToastHost } from "../components/ToastHost";
import { ConfirmHost } from "../components/ConfirmDialog";
import { useUIStore } from "../stores/session";
import { initWebVitals } from "../utils/webVitals";
import { systemApi } from "../api";
import {
  IconChevronLeft,
  IconChevronRight,
  IconMoon,
  IconSun,
  IconMenu,
} from "../components/Icon";
import { NAV_ITEMS } from "../config/nav";
import {
  TaskWorkbench,
  CapabilityCenter,
  OperationsPage,
  Settings,
  Diagnostics,
  KnowledgeLibrary,
  DataCenter,
  MemoryPage,
  ReviewCenter,
  RuntimeAudit,
  preloadRoute,
} from "../routes";

function formatVersion(version: string): string {
  return version.startsWith("v") ? version : `v${version}`;
}

const NavItem = memo(function NavItem({ to, label, testid, Icon }: import("../config/nav").NavItem) {
  const handleEnter = useCallback(() => preloadRoute(to), [to]);
  const handleFocus = useCallback(() => preloadRoute(to), [to]);
  return (
    <NavLink
      key={to}
      to={to}
      data-testid={testid}
      className={({ isActive }) => "app-nav-item" + (isActive ? " active" : "")}
      onMouseEnter={handleEnter}
      onFocus={handleFocus}
    >
      <Icon size={14} />
      <span>{label}</span>
    </NavLink>
  );
});

/** Per-route skeleton shown while a lazily-loaded page chunk is fetched, so
 *  navigation feels instant instead of flashing an empty spinner. */
const SKELETON_BY_PATH: Record<string, "list" | "table"> = {
  "/workbench": "list",
  "/runs": "list",
  "/audit": "table",
  "/reviews": "list",
  "/knowledge": "list",
  "/data": "table",
  "/memory": "list",
  "/diagnostics": "list",
  "/capabilities": "list",
};

function RouteFallback() {
  const { pathname } = useLocation();
  const kind = SKELETON_BY_PATH[pathname] ?? "list";
  return (
    <div className="route-fallback route-skeleton" role="status" aria-live="polite" aria-busy="true">
      <div className="route-skeleton-inner">
        {kind === "table" ? <SkeletonTable rows={8} cols={4} /> : <SkeletonList rows={9} />}
      </div>
      <span className="sr-only">页面加载中…</span>
    </div>
  );
}

function AppShell() {
  const [version, setVersion] = useState<string | null>(null);
  const theme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const toggleMobileNav = useUIStore((s) => s.toggleMobileNav);
  const setMobileNavOpen = useUIStore((s) => s.setMobileNavOpen);

  const location = useLocation();

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // Best-effort RUM: ship Core Web Vitals to the backend (silently no-ops if absent).
  useEffect(() => {
    initWebVitals();
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    systemApi
      .version(ctrl.signal)
      .then((res) => setVersion(res.version || "unknown"))
      .catch(() => setVersion(null));
    return () => ctrl.abort();
  }, []);

  // Close the off-canvas drawer whenever the route changes.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname, setMobileNavOpen]);

  return (
    <div className="app-shell">
      <header className="app-header">
        <button
          type="button"
          className="nav-toggle"
          data-testid="btn-mobile-nav"
          aria-label={mobileNavOpen ? "关闭导航" : "打开导航"}
          aria-expanded={mobileNavOpen}
          aria-controls="layout-left"
          onClick={toggleMobileNav}
        >
          {mobileNavOpen ? <IconChevronLeft size={16} /> : <IconMenu size={16} />}
        </button>

        <Link className="brand" to="/" aria-label="Agent Platform Base">
          <span className="brand-text">
            <span>Agent Platform Base</span>
            <small>Agent App Starter{version ? ` · ${formatVersion(version)}` : ""}</small>
          </span>
        </Link>

        <nav className="app-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => <NavItem key={item.to} {...item} />)}
        </nav>

        <div className="app-spacer" />

        <button
          type="button"
          className="collapse-btn"
          data-tip="切换侧栏"
          data-testid="btn-toggle-sidebar"
          aria-label="切换侧栏"
          aria-expanded={sidebarOpen}
          onClick={toggleSidebar}
        >
          {sidebarOpen ? <IconChevronLeft size={14} /> : <IconChevronRight size={14} />}
        </button>

        <button
          type="button"
          className="theme-toggle"
          data-tip={theme === "dark" ? "切换浅色" : "切换深色"}
          aria-label="切换主题"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        >
          {theme === "dark" ? <IconSun size={14} /> : <IconMoon size={14} />}
        </button>
      </header>

      <div className="app-main">
        {/* AppLayout renders the persistent sidebar + main grid once; the
            Suspense boundary keeps it visible while a route's chunk loads,
            so navigation never tears down the shell. */}
        <AppLayout>
          <Suspense fallback={<RouteFallback />}>
            <div className="route-view">
              <Routes>
                <Route path="/workbench" element={<ErrorBoundary><TaskWorkbench /></ErrorBoundary>} />
                <Route path="/knowledge" element={<ErrorBoundary><KnowledgeLibrary /></ErrorBoundary>} />
                <Route path="/data" element={<ErrorBoundary><DataCenter /></ErrorBoundary>} />
                <Route path="/memory" element={<ErrorBoundary><MemoryPage /></ErrorBoundary>} />
                <Route path="/capabilities" element={<ErrorBoundary><CapabilityCenter /></ErrorBoundary>} />
                <Route path="/diagnostics" element={<ErrorBoundary><Diagnostics /></ErrorBoundary>} />
                <Route path="/settings" element={<ErrorBoundary><Settings /></ErrorBoundary>} />
                <Route path="/runs" element={<ErrorBoundary><OperationsPage /></ErrorBoundary>} />
                <Route path="/audit" element={<ErrorBoundary><RuntimeAudit /></ErrorBoundary>} />
                <Route path="/reviews" element={<ErrorBoundary><ReviewCenter /></ErrorBoundary>} />
                <Route path="/" element={<Navigate to="/workbench" replace />} />
                <Route
                  path="*"
                  element={
                    <ErrorBoundary>
                      <div className="hero">
                        <div className="hero-mark">404</div>
                        <h1 className="hero-title">页面不存在</h1>
                        <p className="hero-sub">请通过顶栏导航回到工作台</p>
                      </div>
                    </ErrorBoundary>
                  }
                />
              </Routes>
            </div>
          </Suspense>
        </AppLayout>
      </div>
      <ToastHost />
      <ConfirmHost />
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <AppShell />
    </BrowserRouter>
  );
}
