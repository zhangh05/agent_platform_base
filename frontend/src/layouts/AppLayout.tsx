import type { ReactNode } from "react";
import { memo, useCallback, useEffect, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useUIStore } from "../stores/session";
import { Sidebar } from "./Sidebar";
import { NAV_ITEMS } from "../config/nav";
import { preloadRoute } from "../routes";
import type { NavItem } from "../config/nav";

interface AppLayoutProps {
  children: ReactNode;
}

const MobileNavItem = memo(function MobileNavItem({ to, label, testid, Icon }: NavItem) {
  const handleEnter = useCallback(() => preloadRoute(to), [to]);
  const handleFocus = useCallback(() => preloadRoute(to), [to]);
  return (
    <NavLink
      key={to}
      to={to}
      data-testid={`mobile-${testid}`}
      className={({ isActive }) => "mobile-nav-item" + (isActive ? " active" : "")}
      onMouseEnter={handleEnter}
      onFocus={handleFocus}
    >
      <Icon size={15} />
      <span>{label}</span>
    </NavLink>
  );
});

/**
 * Two-column app layout using CSS grid.
 * - Sidebar: 280px, collapsible (desktop)
 * - Main: flex-1, scrollable
 * - ≤900px: the sidebar becomes an off-canvas drawer toggled by `mobileNavOpen`
 *   (the hamburger button in the header). A backdrop closes it.
 *
 * v3.9: Inspector panel removed — diagnostics moved inline to Timeline.
 */
export function AppLayout({ children }: AppLayoutProps) {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const setMobileNavOpen = useUIStore((s) => s.setMobileNavOpen);
  const location = useLocation();
  const mainRef = useRef<HTMLElement | null>(null);

  // On route change, move focus to the main region so keyboard / screen-reader
  // users are not stranded on the link they clicked.
  useEffect(() => {
    mainRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);

  const rootClasses = [
    "app-root",
    !sidebarOpen ? "no-sidebar" : "",
    mobileNavOpen ? "mobile-nav-open" : "",
  ].filter(Boolean).join(" ");

  // Lock body scroll + wire Escape-to-close + trap focus inside the drawer
  // while it's open. WCAG 2.1 SC 2.4.3 (Focus Order) — Tab must not move
  // focus to elements hidden behind the modal drawer.
  const drawerRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!mobileNavOpen) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Move focus into the drawer so keyboard navigation starts inside it.
    const drawer = drawerRef.current;
    const firstFocusable = drawer?.querySelector<HTMLElement>(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"]), input, select, textarea',
    );
    // Remember the element that opened the drawer so we can restore focus.
    const previouslyFocused = document.activeElement as HTMLElement | null;
    firstFocusable?.focus({ preventScroll: true });

    const getFocusable = (): HTMLElement[] => {
      if (!drawer) return [];
      return Array.from(
        drawer.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
        ),
      );
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMobileNavOpen(false);
        return;
      }
      if (e.key !== "Tab") return;
      const items = getFocusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);

    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
      // Restore focus to the trigger so keyboard users land where they were.
      previouslyFocused?.focus({ preventScroll: true });
    };
  }, [mobileNavOpen, setMobileNavOpen]);

  // Auto-close the drawer when the viewport grows back to desktop size.
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 901px)");
    const onChange = () => {
      if (mq.matches && mobileNavOpen) setMobileNavOpen(false);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mobileNavOpen, setMobileNavOpen]);

  return (
    <div className={rootClasses}>
      <a className="skip-link" href="#main">跳到主内容</a>
      <aside
        ref={drawerRef}
        className={"app-sidebar" + (sidebarOpen ? "" : " collapsed")}
        data-testid="layout-left"
        aria-label="侧栏"
        aria-modal={mobileNavOpen ? "true" : undefined}
      >
        {(sidebarOpen || mobileNavOpen) && (
          <div className="sidebar-scroll">
            <nav className="mobile-nav" aria-label="页面导航">
              {NAV_ITEMS.map((item) => <MobileNavItem key={item.to} {...item} />)}
            </nav>
            <Sidebar />
          </div>
        )}
      </aside>

      {mobileNavOpen && (
        <div
          className="nav-backdrop"
          data-testid="nav-backdrop"
          onClick={() => setMobileNavOpen(false)}
          aria-hidden="true"
        />
      )}

      <section className="app-main" id="main" tabIndex={-1} ref={mainRef} data-testid="layout-center">
        {children}
      </section>
    </div>
  );
}
