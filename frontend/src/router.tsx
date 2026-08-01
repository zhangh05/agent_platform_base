import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from "react";

export type AppLocation = {
  pathname: string;
  search: string;
  hash: string;
};

type RouterValue = {
  location: AppLocation;
  navigate: (target: string, options?: { replace?: boolean }) => void;
};

const RouterContext = createContext<RouterValue | null>(null);

function safeTarget(raw: string): string {
  const value = String(raw || "").trim();
  if (!value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u001f]/.test(value)) {
    throw new Error("Only same-origin application paths are allowed");
  }
  const parsed = new URL(value, window.location.origin);
  if (parsed.origin !== window.location.origin) {
    throw new Error("Cross-origin navigation is not allowed");
  }
  return `${parsed.pathname}${parsed.search}${parsed.hash}`;
}

function readWindowLocation(): AppLocation {
  return {
    pathname: window.location.pathname || "/",
    search: window.location.search,
    hash: window.location.hash,
  };
}

export function BrowserRouter({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<AppLocation>(readWindowLocation);

  useEffect(() => {
    const onPopState = () => setLocation(readWindowLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((raw: string, options?: { replace?: boolean }) => {
    const target = safeTarget(raw);
    window.history[options?.replace ? "replaceState" : "pushState"]({}, "", target);
    setLocation(readWindowLocation());
  }, []);

  return <RouterContext.Provider value={{ location, navigate }}>{children}</RouterContext.Provider>;
}

export function MemoryRouter({ children, initialEntries = ["/"] }: { children: ReactNode; initialEntries?: string[] }) {
  const initial = safeTarget(initialEntries[0] || "/");
  const [target, setTarget] = useState(initial);
  const location = useMemo<AppLocation>(() => {
    const parsed = new URL(target, window.location.origin);
    return { pathname: parsed.pathname, search: parsed.search, hash: parsed.hash };
  }, [target]);
  const navigate = useCallback((next: string) => setTarget(safeTarget(next)), []);
  return <RouterContext.Provider value={{ location, navigate }}>{children}</RouterContext.Provider>;
}

export function useLocation(): AppLocation {
  const router = useContext(RouterContext);
  if (!router) throw new Error("Router context is required");
  return router.location;
}

export function useNavigate() {
  const router = useContext(RouterContext);
  if (!router) throw new Error("Router context is required");
  return router.navigate;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href" | "className"> & {
  to: string;
  className?: string | ((state: { isActive: boolean }) => string);
  viewTransition?: boolean;
};

function InternalLink({ to, className, viewTransition, onClick, children, activeAware: _activeAware, ...props }: LinkProps & { activeAware?: boolean }) {
  const { location, navigate } = useContext(RouterContext) ?? {};
  if (!location || !navigate) throw new Error("Router context is required");
  const target = safeTarget(to);
  const pathname = new URL(target, window.location.origin).pathname;
  const isActive = location.pathname === pathname;
  const resolvedClass = typeof className === "function" ? className({ isActive }) : className;

  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const perform = () => navigate(target);
    const documentWithTransition = document as Document & { startViewTransition?: (callback: () => void) => void };
    if (viewTransition && documentWithTransition.startViewTransition) {
      documentWithTransition.startViewTransition(perform);
    } else {
      perform();
    }
  };

  return <a {...props} href={target} className={resolvedClass} onClick={handleClick}>{children}</a>;
}

export function Link(props: LinkProps) {
  return <InternalLink {...props} />;
}

export function NavLink(props: LinkProps) {
  return <InternalLink {...props} activeAware />;
}

export function Navigate({ to, replace = false }: { to: string; replace?: boolean }) {
  const navigate = useNavigate();
  useEffect(() => navigate(to, { replace }), [navigate, replace, to]);
  return null;
}

type SearchParamsInit = URLSearchParams | Record<string, string> | Array<[string, string]>;

export function useSearchParams(): [URLSearchParams, (next: SearchParamsInit, options?: { replace?: boolean }) => void] {
  const location = useLocation();
  const navigate = useNavigate();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const setParams = useCallback((next: SearchParamsInit, options?: { replace?: boolean }) => {
    const query = new URLSearchParams(next).toString();
    navigate(`${location.pathname}${query ? `?${query}` : ""}${location.hash}`, options);
  }, [location.hash, location.pathname, navigate]);
  return [params, setParams];
}
