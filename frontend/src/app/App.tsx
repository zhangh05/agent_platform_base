import { BrowserRouter, Link, Navigate, NavLink, useLocation } from "../router";
import { Suspense, memo, useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { SkeletonList, SkeletonTable } from "../components/common";
import { AppLayout } from "../layouts/AppLayout";
import { ToastHost } from "../components/ToastHost";
import { ConfirmHost } from "../components/ConfirmDialog";
import { useSessionStore, useUIStore } from "../stores/session";
import { useWorkbenchStore } from "../stores/workbench";
import { initWebVitals } from "../utils/webVitals";
import { authApi, systemApi } from "../api";
import { isApiError } from "../types";
import { ACTIVE_USER_KEY, scopedLocalStorageKey, setActiveUserScope, setActiveWorkspaceScope } from "../utils/userScope";
import {
  IconChevronLeft,
  IconChevronRight,
  IconMoon,
  IconSun,
  IconMenu,
} from "../components/Icon";
import { NAV_ITEMS, buildNavGroups } from "../config/nav";
import type { NavGroup } from "../config/nav";
import { ExtensionRegistryProvider, useExtensionRegistry } from "../extensions/registry";
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
  ExtensionCenter,
  WorkflowStudio,
  UserManagement,
  preloadRoute,
  preloadAppRoutes,
} from "../routes";

function formatVersion(version: string): string {
  return version.startsWith("v") ? version : `v${version}`;
}

function clearUserScopedFrontendState(nextSession?: Awaited<ReturnType<typeof authApi.status>>) {
  const allowed = nextSession?.workspace_ids || [];
  const currentWorkspace = useSessionStore.getState().currentWorkspaceId;
  const nextWorkspace = nextSession?.platform_admin
    ? (currentWorkspace || nextSession.home_workspace_id || "default")
    : (nextSession?.home_workspace_id || (allowed.includes(currentWorkspace) ? currentWorkspace : allowed[0]) || "");
  setActiveUserScope(nextSession?.username || "", nextWorkspace);
  useSessionStore.getState().resetForUser(nextWorkspace);
  if (nextSession?.username) void useWorkbenchStore.persist.rehydrate();
  else {
    try {
      localStorage.removeItem(scopedLocalStorageKey("na_workbench"));
      localStorage.removeItem(scopedLocalStorageKey("na_session", false));
    } catch { /* storage can be unavailable */ }
    void useWorkbenchStore.persist.rehydrate();
  }
  try { sessionStorage.removeItem("workbench_auto_prompt"); } catch { /* noop */ }
}

function applyAuthenticatedSession(nextSession: Awaited<ReturnType<typeof authApi.status>>) {
  let previousUsername = "";
  try { previousUsername = localStorage.getItem(ACTIVE_USER_KEY) || ""; } catch { /* noop */ }
  const currentWorkspace = useSessionStore.getState().currentWorkspaceId;
  const allowed = nextSession.workspace_ids || [];
  const invalidWorkspace = !nextSession.platform_admin && !allowed.includes(currentWorkspace);
  if (previousUsername !== nextSession.username || invalidWorkspace) {
    clearUserScopedFrontendState(nextSession);
  } else if (nextSession.home_workspace_id && !currentWorkspace) {
    useSessionStore.getState().setCurrentWorkspace(nextSession.home_workspace_id);
  }
}

const NavGroupItem = memo(function NavGroupItem({ group, currentPath }: { group: NavGroup; currentPath: string }) {
  const active = group.items.some((item) => item.to === currentPath);
  const warmGroup = useCallback(() => {
    void preloadRoute(group.to);
    group.items.forEach((item) => void preloadRoute(item.to));
  }, [group]);
  const Icon = group.Icon;
  const hasMenu = group.items.length > 1;
  return (
    <div className={"app-nav-group" + (active ? " active" : "") + (hasMenu ? " has-menu" : "")} onMouseEnter={warmGroup} onFocus={warmGroup}>
      <NavLink
        to={group.to}
        data-testid={group.testid}
        className={() => "app-nav-item app-nav-group-trigger" + (active ? " active" : "")}
        onPointerDown={warmGroup}
        onTouchStart={warmGroup}
        aria-haspopup={hasMenu ? "menu" : undefined}
        aria-expanded={hasMenu ? (active ? "true" : "false") : undefined}
        viewTransition
      >
        <Icon size={14} />
        <span>{group.label}</span>
      </NavLink>
      {hasMenu ? (
        <div className="app-nav-menu" role="menu" aria-label={group.label}>
          <div className="app-nav-menu-head">
            <strong>{group.label}</strong>
            <span>{group.description}</span>
          </div>
          {group.items.map((item) => {
            const ChildIcon = item.Icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                data-testid={item.testid}
                className={({ isActive }) => "app-nav-menu-item" + (isActive ? " active" : "")}
                onMouseEnter={() => preloadRoute(item.to)}
                onFocus={() => preloadRoute(item.to)}
                viewTransition
                role="menuitem"
              >
                <ChildIcon size={14} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </div>
      ) : null}
    </div>
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

function AppRoutes({ canManageUsers }: { canManageUsers: boolean }) {
  const location = useLocation();
  const extensionRegistry = useExtensionRegistry();
  const routes: Record<string, ReactNode> = {
    "/workbench": <ErrorBoundary><TaskWorkbench /></ErrorBoundary>,
    "/knowledge": <ErrorBoundary><KnowledgeLibrary /></ErrorBoundary>,
    "/data": <ErrorBoundary><DataCenter /></ErrorBoundary>,
    "/memory": <ErrorBoundary><MemoryPage /></ErrorBoundary>,
    "/capabilities": <ErrorBoundary><CapabilityCenter /></ErrorBoundary>,
    "/diagnostics": <ErrorBoundary><Diagnostics /></ErrorBoundary>,
    "/settings": <ErrorBoundary><Settings /></ErrorBoundary>,
    "/runs": <ErrorBoundary><OperationsPage /></ErrorBoundary>,
    "/audit": <ErrorBoundary><RuntimeAudit /></ErrorBoundary>,
    "/reviews": <ErrorBoundary><ReviewCenter /></ErrorBoundary>,
    "/extensions": <ErrorBoundary><ExtensionCenter /></ErrorBoundary>,
    "/workflows": <ErrorBoundary><WorkflowStudio /></ErrorBoundary>,
    "/users": canManageUsers ? <ErrorBoundary><UserManagement /></ErrorBoundary> : <Navigate to="/workbench" replace />,
    "/organizations": <Navigate to={canManageUsers ? "/users" : "/workbench"} replace />,
  };
  const extensionRoute = extensionRegistry.routes.find((route) => route.path === location.pathname);
  const content = location.pathname === "/" ? (
    <Navigate to="/workbench" replace />
  ) : extensionRoute ? (
    <ErrorBoundary><extensionRoute.Component /></ErrorBoundary>
  ) : !extensionRegistry.ready && location.pathname.startsWith("/extensions/") ? (
    <RouteFallback />
  ) : routes[location.pathname] ?? (
    <ErrorBoundary>
      <div className="hero">
        <div className="hero-mark">404</div>
        <h1 className="hero-title">页面不存在</h1>
        <p className="hero-sub">请通过顶栏导航回到工作台</p>
      </div>
    </ErrorBoundary>
  );
  return (
    <Suspense fallback={<RouteFallback />}>
      <div className="route-view" key={location.pathname} data-route={location.pathname}>
        {content}
      </div>
    </Suspense>
  );
}

function LoginScreen({ onLogin }: { onLogin: (status: Awaited<ReturnType<typeof authApi.status>>) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await authApi.login(username.trim(), password);
      onLogin(await authApi.status());
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
          <span className="login-kicker">联智中枢</span>
          <h1 id="login-title">登录工作台</h1>
          <p>请输入账户凭据继续。</p>
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

function AppShell({ canLogout, onLogout, session }: { canLogout: boolean; onLogout: () => void; session: Awaited<ReturnType<typeof authApi.status>> | null }) {
  const [version, setVersion] = useState<string | null>(null);
  const theme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const toggleMobileNav = useUIStore((s) => s.toggleMobileNav);
  const setMobileNavOpen = useUIStore((s) => s.setMobileNavOpen);
  const currentWorkspaceId = useSessionStore((s) => s.currentWorkspaceId);

  const location = useLocation();
  const extensionRegistry = useExtensionRegistry();
  const canManageUsers = session?.platform_admin === true;
  const navigationItems = [...NAV_ITEMS.filter((item) => !item.adminOnly || canManageUsers), ...extensionRegistry.navItems];
  const navigationGroups = useMemo(() => buildNavGroups(navigationItems), [navigationItems]);

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
    if (!session?.username || !currentWorkspaceId) return;
    setActiveWorkspaceScope(currentWorkspaceId);
    void useWorkbenchStore.persist.rehydrate();
  }, [session?.username, currentWorkspaceId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void preloadAppRoutes(location.pathname, navigationItems.map((item) => item.to));
    }, 700);
    return () => window.clearTimeout(timer);
  }, [canManageUsers]); // eslint-disable-line react-hooks/exhaustive-deps

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

        <Link className="brand" to="/workbench" aria-label="联智中枢" viewTransition>
          <span className="brand-text">
            <span>联智中枢</span>
            <small>{version ? formatVersion(version) : ""}</small>
          </span>
        </Link>

        <nav className="app-nav" aria-label="主导航">
          {navigationGroups.map((group) => <NavGroupItem key={group.id} group={group} currentPath={location.pathname} />)}
        </nav>

        <div className="app-actions" aria-label="页面操作">
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
            title={session?.username ? `当前用户：${session.username}` : "退出登录"}
            onClick={onLogout}
            >
              退出
            </button>
          ) : null}
        </div>
      </header>

      <div className="app-main">
        {/* AppLayout renders the persistent sidebar + main grid once; the
            Suspense boundary keeps it visible while a route's chunk loads,
            so navigation never tears down the shell. */}
        <AppLayout navigationItems={navigationItems}>
          <AppRoutes canManageUsers={canManageUsers} />
        </AppLayout>
      </div>
      <ToastHost />
      <ConfirmHost />
    </div>
  );
}

export function App() {
  const [authState, setAuthState] = useState<"checking" | "public" | "authenticated" | "login">("checking");
  const [session, setSession] = useState<Awaited<ReturnType<typeof authApi.status>> | null>(null);
  const [showAuthLoading, setShowAuthLoading] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    const loadingTimer = window.setTimeout(() => setShowAuthLoading(true), 280);
    authApi
      .status(ctrl.signal)
      .then((res) => {
        clearTimeout(loadingTimer);
        if (!res.login_enabled) {
          setSession(res);
          setAuthState("public");
          return;
        }
        if (res.authenticated) {
          applyAuthenticatedSession(res);
          setSession(res);
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

  const handleLogin = useCallback((nextSession: Awaited<ReturnType<typeof authApi.status>>) => {
    applyAuthenticatedSession(nextSession);
    setSession(nextSession);
    setAuthState("authenticated");
  }, []);

  const handleLogout = useCallback(() => {
    authApi.logout().finally(() => {
      clearUserScopedFrontendState();
      setSession(null);
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
    <BrowserRouter>
      <ExtensionRegistryProvider>
        <AppShell canLogout={authState === "authenticated"} onLogout={handleLogout} session={session} />
      </ExtensionRegistryProvider>
    </BrowserRouter>
  );
}
