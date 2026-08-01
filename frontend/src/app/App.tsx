import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Suspense, memo, useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { SkeletonList, SkeletonTable } from "../components/common";
import { AppLayout } from "../layouts/AppLayout";
import { ToastHost } from "../components/ToastHost";
import { ConfirmHost } from "../components/ConfirmDialog";
import { useUIStore } from "../stores/session";
import { initWebVitals } from "../utils/webVitals";
import { authApi, systemApi } from "../api";
import { isApiError } from "../types";
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
  preloadAppRoutes,
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
      onPointerDown={handleEnter}
      onTouchStart={handleEnter}
      viewTransition
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

function AppRoutes() {
  const location = useLocation();
  return (
    <Suspense fallback={<RouteFallback />}>
      <div className="route-view" key={location.pathname} data-route={location.pathname}>
        <Routes location={location}>
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
  );
}

function LoginScreen({ onLogin }: { onLogin: (username: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const res = await authApi.login(username.trim(), password);
      onLogin(res.username || username.trim());
    } catch (err) {
      if (isApiError(err) && err.status === 403) {
        setError("当前访问地址未被后端允许，请联系管理员检查访问地址配置");
      } else if (isApiError(err) && err.code === "network") {
        setError("无法连接后端服务，请检查访问地址或端口是否放通");
      } else {
        setError("账号或密码不正确");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="login-title">
        <div className="login-brand">
          <span className="login-kicker">Agent Platform Base</span>
          <h1 id="login-title">登录工作台</h1>
          <p>请输入管理员凭据继续。</p>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            <span>账户</span>
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              disabled={submitting}
            />
          </label>
          <label>
            <span>密码</span>
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={submitting}
              autoFocus
            />
          </label>
          {error ? <div className="login-error" role="alert">{error}</div> : null}
          <button type="submit" className="login-submit" disabled={submitting || !username.trim() || !password}>
            {submitting ? "正在登录…" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}

function AppShell({ canLogout, onLogout, username }: { canLogout: boolean; onLogout: () => void; username: string }) {
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

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void preloadAppRoutes(location.pathname);
    }, 700);
    return () => window.clearTimeout(timer);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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

        <Link className="brand" to="/workbench" aria-label="Agent Platform Base" viewTransition>
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

        {canLogout ? (
          <button
            type="button"
            className="logout-btn"
            aria-label="退出登录"
            title={username ? `当前用户：${username}` : "退出登录"}
            onClick={onLogout}
          >
            退出
          </button>
        ) : null}
      </header>

      <div className="app-main">
        {/* AppLayout renders the persistent sidebar + main grid once; the
            Suspense boundary keeps it visible while a route's chunk loads,
            so navigation never tears down the shell. */}
        <AppLayout>
          <AppRoutes />
        </AppLayout>
      </div>
      <ToastHost />
      <ConfirmHost />
    </div>
  );
}

export function App() {
  const [authState, setAuthState] = useState<"checking" | "public" | "authenticated" | "login">("checking");
  const [username, setUsername] = useState("");
  const [showAuthLoading, setShowAuthLoading] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    const loadingTimer = window.setTimeout(() => setShowAuthLoading(true), 280);
    authApi
      .status(ctrl.signal)
      .then((res) => {
        clearTimeout(loadingTimer);
        if (!res.login_enabled) {
          setAuthState("public");
          return;
        }
        if (res.authenticated) {
          setUsername(res.username || "");
          setAuthState("authenticated");
          return;
        }
        setAuthState("login");
      })
      .catch((err) => {
        clearTimeout(loadingTimer);
        // React StrictMode intentionally mounts, cleans up, and mounts effects
        // again in development. The first auth request is therefore aborted on
        // refresh; treating that cancellation as an authentication failure
        // briefly mounts LoginScreen before the second request succeeds.
        if (ctrl.signal.aborted || (isApiError(err) && err.code === "aborted")) {
          return;
        }
        setAuthState("login");
      });
    return () => {
      clearTimeout(loadingTimer);
      ctrl.abort();
    };
  }, []);

  const handleLogin = useCallback((nextUsername: string) => {
    setUsername(nextUsername);
    setAuthState("authenticated");
  }, []);

  const handleLogout = useCallback(() => {
    authApi.logout().finally(() => {
      setUsername("");
      setAuthState("login");
    });
  }, []);

  if (authState === "checking") {
    return <div className={`auth-loading${showAuthLoading ? " visible" : ""}`} role="status" aria-label="加载中" />;
  }

  if (authState === "login") {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return (
    <BrowserRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <AppShell canLogout={authState === "authenticated"} onLogout={handleLogout} username={username} />
    </BrowserRouter>
  );
}
