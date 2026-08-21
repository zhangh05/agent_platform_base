// src/routes.tsx
//
// Route-level code splitting keeps secondary pages in on-demand chunks.
// The workbench is the default product route and is loaded with the app shell,
// so a dynamic chunk cannot leave the primary session surface in a Suspense
// fallback when a navigation or cache handoff is interrupted.

// `lazyWithPreload` also exposes each page's import promise as `.preload`,
// so the shell can warm a page's chunk on nav hover/focus (see App.tsx and
// AppLayout.tsx). By the time the user clicks, the chunk is usually already
// in cache and the route swaps instantly.

import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import { TaskWorkbench } from "./pages/AgentWorkbench/AgentWorkbench";
export { TaskWorkbench };

type AnyComp = ComponentType<any>;
type PageModule = Promise<{ default: AnyComp }>;

function lazyWithPreload(
  factory: () => PageModule,
): LazyExoticComponent<AnyComp> & { preload: () => PageModule } {
  const Comp = lazy(factory) as LazyExoticComponent<AnyComp> & {
    preload: () => PageModule;
  };
  Comp.preload = factory;
  return Comp;
}

export const CapabilityCenter = lazyWithPreload(() =>
  import("./pages/CapabilityCenter/CapabilityCenter").then((m) => ({ default: m.CapabilityCenter })),
);
export const OperationsPage = lazyWithPreload(() =>
  import("./pages/Operations/OperationsPage").then((m) => ({ default: m.OperationsPage })),
);
export const Settings = lazyWithPreload(() =>
  import("./pages/Settings/Settings").then((m) => ({ default: m.Settings })),
);
export const Diagnostics = lazyWithPreload(() =>
  import("./pages/Diagnostics/Diagnostics").then((m) => ({ default: m.Diagnostics })),
);
export const KnowledgeLibrary = lazyWithPreload(() =>
  import("./pages/KnowledgeLibrary/KnowledgeLibrary").then((m) => ({ default: m.KnowledgeLibrary })),
);
export const DataCenter = lazyWithPreload(() =>
  import("./pages/DataCenter/DataCenter").then((m) => ({ default: m.DataCenter })),
);
export const MemoryPage = lazyWithPreload(() =>
  import("./pages/MemoryPage/MemoryPage").then((m) => ({ default: m.MemoryPage })),
);
export const ReviewCenter = lazyWithPreload(() =>
  import("./pages/ReviewCenter/ReviewCenter").then((m) => ({ default: m.ReviewCenter })),
);
export const ExtensionCenter = lazyWithPreload(() =>
  import("./pages/ExtensionCenter/ExtensionCenter").then((m) => ({ default: m.ExtensionCenter })),
);
export const WorkflowStudio = lazyWithPreload(() =>
  import("./pages/WorkflowStudio/WorkflowStudio").then((m) => ({ default: m.WorkflowStudio })),
);
export const UserManagement = lazyWithPreload(() =>
  import("./pages/UserManagement/UserManagement").then((m) => ({ default: m.UserManagement })),
);
export const AdvancedCenter = lazyWithPreload(() =>
  import("./pages/AdvancedCenter/AdvancedCenter").then((m) => ({ default: m.AdvancedCenter })),
);
// Path → preload thunk. Keys match `NAV_ITEMS.to` plus the secondary routes.
const PRELOAD: Record<string, () => PageModule> = {
  "/workbench": () => Promise.resolve({ default: TaskWorkbench }),
  "/knowledge": KnowledgeLibrary.preload,
  "/data": DataCenter.preload,
  "/memory": MemoryPage.preload,
  "/capabilities": CapabilityCenter.preload,
  "/diagnostics": Diagnostics.preload,
  "/settings": Settings.preload,
  "/runs": OperationsPage.preload,
  "/reviews": ReviewCenter.preload,
  "/extensions": ExtensionCenter.preload,
  "/workflows": WorkflowStudio.preload,
  "/users": UserManagement.preload,
  "/advanced": AdvancedCenter.preload,
};

const ROUTE_PATHS = Object.keys(PRELOAD);

/** Warm a route's chunk ahead of navigation (call on hover/focus). */
export function preloadRoute(path: string): Promise<void> {
  const loader = PRELOAD[path];
  return loader ? loader().then(() => undefined) : Promise.resolve();
}

/** Warm every remaining page chunk once the initial screen is settled. */
export async function preloadAppRoutes(currentPath = "", allowedPaths?: string[]): Promise<void> {
  const allowed = allowedPaths ? new Set(allowedPaths) : null;
  const paths = ROUTE_PATHS.filter((path) => path !== currentPath && (!allowed || allowed.has(path)));
  // Load sequentially so background warming never competes with the active
  // page's API requests on slower links.
  for (const path of paths) {
    try {
      await preloadRoute(path);
    } catch {
      // Navigation can retry the import normally; warming is best-effort.
    }
  }
}
