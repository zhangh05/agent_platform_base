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
  Icon: ComponentType<{ size?: number }>;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/workbench", label: "对话工作台", testid: "nav-workbench", Icon: IconChat },
  { to: "/runs", label: "任务记录", testid: "nav-runs", Icon: IconHistory },
  { to: "/capabilities", label: "工具与能力", testid: "nav-capabilities", Icon: IconLayers },
  { to: "/knowledge", label: "知识库", testid: "nav-knowledge", Icon: IconBook },
  { to: "/data", label: "数据管理", testid: "nav-data", Icon: IconBox },
  { to: "/memory", label: "长期记忆", testid: "nav-memory", Icon: IconBrain },
  { to: "/diagnostics", label: "系统状态", testid: "nav-diagnostics", Icon: IconProbe },
  { to: "/settings", label: "系统设置", testid: "nav-settings", Icon: IconSettings },
  { to: "/extensions", label: "扩展管理", testid: "nav-extensions", Icon: IconLayers },
  { to: "/workflows", label: "应用编排", testid: "nav-workflows", Icon: IconLayers },
  { to: "/organizations", label: "组织与成员", testid: "nav-organizations", Icon: IconSettings },
];
