import {
  createContext,
  lazy,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";
import { extensionsApi, type InstalledExtension } from "../api";
import { IconBox } from "../components/Icon";
import type { NavItem } from "../config/nav";

type Loader = () => Promise<{ default: ComponentType }>;
type ExtensionRoute = { path: string; Component: React.LazyExoticComponent<ComponentType> };
type RegistryState = { ready: boolean; extensions: InstalledExtension[]; navItems: NavItem[]; routes: ExtensionRoute[]; reload: () => Promise<void> };
type BundledManifest = { extension_id?: string };

/*
 * The module glob is the canonical build-time catalogue. Explicit suffixes make
 * built-in manifest routes deterministic even when Vite normalizes glob keys
 * differently between development and production builds. No runtime plugin code
 * is imported outside the manifest/registry contract.
 */
const moduleLoaders = {
  ...import.meta.glob("../../../extensions/*/frontend/*.{ts,tsx}"),
  ...import.meta.glob("../../../plugins/*/frontend/*.{ts,tsx}"),
} as Record<string, Loader>;
const bundledManifests = {
  ...import.meta.glob("../../../extensions/*/extension.json", { eager: true, import: "default" }),
  ...import.meta.glob("../../../plugins/*/extension.json", { eager: true, import: "default" }),
} as Record<string, BundledManifest>;
const bundledModuleLoaders: Record<string, Loader> = {
  "network.operations:frontend/NetworkOperations.tsx": () => import("@lzcore-extension/network-operations"),
  "reference.insights:frontend/ReferenceInsights.tsx": () => import("@lzcore-extension/reference-insights"),
};

function loaderFor(extensionId: string, modulePath: string): Loader | undefined {
  const bundledLoader = bundledModuleLoaders[`${extensionId}:${modulePath}`];
  if (bundledLoader) return bundledLoader;
  const manifestEntry = Object.entries(bundledManifests).find(([, manifest]) => manifest.extension_id === extensionId);
  if (!manifestEntry) return undefined;
  const directory = manifestEntry[0].replace(/\/extension\.json$/, "");
  return moduleLoaders[`${directory}/${modulePath}`];
}

const ExtensionRegistryContext = createContext<RegistryState>({ ready: false, extensions: [], navItems: [], routes: [], reload: async () => {} });

export function ExtensionRegistryProvider({ children }: { children: ReactNode }) {
  const [extensions, setExtensions] = useState<InstalledExtension[]>([]);
  const [ready, setReady] = useState(false);
  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await extensionsApi.list(signal);
      if (!signal?.aborted) setExtensions(response.extensions || []);
    } catch {
      if (!signal?.aborted) setExtensions([]);
    } finally {
      if (!signal?.aborted) setReady(true);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);
  const value = useMemo<RegistryState>(() => {
    const records = extensions
      .filter((extension) => extension.lifecycle?.enabled !== false)
      .flatMap((extension) => (extension.frontend_routes || []).flatMap((route) => {
        const loader = loaderFor(extension.extension_id, route.module);
        return loader ? [{ extension, route, Component: lazy(loader) }] : [];
      }))
      .sort((a, b) => (a.route.order ?? 100) - (b.route.order ?? 100));
    return {
      ready,
      extensions,
      navItems: records.map(({ extension, route }) => ({ to: route.path, label: route.label || extension.name, testid: `nav-extension-${extension.extension_id.replace(/[^a-z0-9]+/gi, "-")}`, Icon: IconBox })),
      routes: records.map(({ route, Component }) => ({ path: route.path, Component })),
      reload: () => reload(),
    };
  }, [extensions, ready, reload]);
  return <ExtensionRegistryContext.Provider value={value}>{children}</ExtensionRegistryContext.Provider>;
}

export function useExtensionRegistry(): RegistryState { return useContext(ExtensionRegistryContext); }
