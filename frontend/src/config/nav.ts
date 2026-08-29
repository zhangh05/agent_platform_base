import type { ComponentType } from "react";
import {
  IconBox,
  IconBook,
  IconBrain,
  IconChat,
  IconHistory,
  IconLayers,
  IconProbe,
  IconSettings,
} from "../components/Icon";

export interface NavItem {
  to: string;
  label: string;
  testid: string;
  Icon: ComponentType<{ size?: string | number }>;
  adminOnly?: boolean;
  /** Render this route from a compact utility menu instead of the main nav. */
  utility?: "settings";
}

export interface NavGroup {
  id: "workbench" | "tasks" | "materials" | "capabilities" | "system";
  label: string;
  description: string;
  to: string;
  testid: string;
  Icon: ComponentType<{ size?: string | number }>;
  items: NavItem[];
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/workbench", label: "工作台", testid: "nav-workbench", Icon: IconChat },
  { to: "/runs", label: "任务记录", testid: "nav-runs", Icon: IconHistory },
  { to: "/capabilities", label: "内置工具", testid: "nav-capabilities", Icon: IconLayers },
  { to: "/knowledge", label: "知识库", testid: "nav-knowledge", Icon: IconBook },
  { to: "/data", label: "文件与数据", testid: "nav-data", Icon: IconBox },
  { to: "/memory", label: "记忆", testid: "nav-memory", Icon: IconBrain },
  { to: "/diagnostics", label: "系统状态", testid: "nav-diagnostics", Icon: IconProbe },
  { to: "/settings", label: "设置", testid: "nav-settings", Icon: IconSettings, utility: "settings" },
  { to: "/users", label: "用户与权限", testid: "nav-users", Icon: IconSettings, adminOnly: true, utility: "settings" },
];


const GROUP_META: Omit<NavGroup, "items">[] = [
  { id: "workbench", label: "工作台", description: "开始对话、上传材料、获取结果", to: "/workbench", testid: "nav-group-workbench", Icon: IconChat },
  { id: "tasks", label: "任务", description: "查看任务进度与处理证据", to: "/runs", testid: "nav-group-tasks", Icon: IconHistory },
  { id: "materials", label: "资料中心", description: "管理文件、知识和记忆", to: "/data", testid: "nav-group-materials", Icon: IconBox },
  { id: "capabilities", label: "能力中心", description: "查看系统能力与可调用工具", to: "/capabilities", testid: "nav-group-capabilities", Icon: IconLayers },
  { id: "system", label: "系统管理", description: "监控系统状态与运行设置", to: "/diagnostics", testid: "nav-group-system", Icon: IconSettings },
];

const GROUP_BY_PATH: Record<string, NavGroup["id"]> = {
  "/workbench": "workbench",
  "/runs": "tasks",
  "/audit": "tasks",
  "/data": "materials",
  "/knowledge": "materials",
  "/memory": "materials",
  "/capabilities": "capabilities",
  "/diagnostics": "system",
  "/settings": "system",
  "/users": "system",
};

function groupForItem(item: NavItem): NavGroup["id"] {
  if (item.to.includes("network.operations")) return "capabilities";
  if (item.to.startsWith("/extensions/")) return "capabilities";
  return GROUP_BY_PATH[item.to] || "capabilities";
}

export function buildNavGroups(items: NavItem[]): NavGroup[] {
  const grouped = new Map<NavGroup["id"], NavItem[]>();
  for (const item of items) {
    const groupId = groupForItem(item);
    grouped.set(groupId, [...(grouped.get(groupId) || []), item]);
  }
  return GROUP_META.map((meta) => ({
    ...meta,
    items: grouped.get(meta.id) || [],
  })).filter((group) => group.items.length > 0);
}
