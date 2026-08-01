import {
  createContext,
  lazy,
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
type ExtensionRoute = {
  path: string;
  Component: React.LazyExoticComponent<ComponentType>;
};
type RegistryState = {
  ready: boolean;
  extensions: InstalledExtension[];
  navItems: NavItem[];
  routes: ExtensionRoute[];
};

type BundledManifest = { extension_id?: string };

const moduleLoaders = {
  ...import.meta.glob("../../../extensions/*/frontend/*.{ts,tsx}"),
  ...import.meta.glob("../../../plugins/*/frontend/*.{ts,tsx}"),
} as Record<string, Loader>;
const bundledManifests = {
  ...import.meta.glob("../../../extensions/*/extension.json", { eager: true, import: "default" }),
  ...import.meta.glob("../../../plugins/*/extension.json", { eager: true, import: "default" }),
} as Record<string, BundledManifest>;

function loaderFor(extensionId: string, modulePath: string): Loader | undefined {
  const manifestEntry = Object.entries(bundledManifests).find(
    ([, manifest]) => manifest.extension_id === extensionId,
  );
  if (!manifestEntry) return undefined;
  const directory = manifestEntry[0].replace(/\/extension\.json$/, "");
  return moduleLoaders[`${directory}/${modulePath}`];
}

const ExtensionRegistryContext = createContext<RegistryState>({
  ready: false,
  extensions: [],
  navItems: [],
  routes: [],
});

export function ExtensionRegistryProvider({ children }: { children: ReactNode }) {
  const [extensions, setExtensions] = useState<InstalledExtension[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    extensionsApi.list(controller.signal)
      .then((response) => setExtensions(response.extensions || []))
      .catch(() => setExtensions([]))
      .finally(() => setReady(true));
    return () => controller.abort();
  }, []);

  const value = useMemo<RegistryState>(() => {
    const records = extensions.flatMap((extension) =>
      (extension.frontend_routes || []).flatMap((route) => {
        const loader = loaderFor(extension.extension_id, route.module);
        if (!loader) return [];
        return [{ extension, route, Component: lazy(loader) }];
      }),
    ).sort((a, b) => (a.route.order ?? 100) - (b.route.order ?? 100));
    return {
      ready,
      extensions,
      navItems: records.map(({ extension, route }) => ({
        to: route.path,
        label: route.label || extension.name,
        testid: `nav-extension-${extension.extension_id.replace(/[^a-z0-9]+/gi, "-")}`,
        Icon: IconBox,
      })),
      routes: records.map(({ route, Component }) => ({ path: route.path, Component })),
    };
  }, [extensions, ready]);

  return <ExtensionRegistryContext.Provider value={value}>{children}</ExtensionRegistryContext.Provider>;
}

export function useExtensionRegistry(): RegistryState {
  return useContext(ExtensionRegistryContext);
}
