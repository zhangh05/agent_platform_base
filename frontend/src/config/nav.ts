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
  { to: "/workbench", label: "工作台", testid: "nav-workbench", Icon: IconChat },
  { to: "/runs", label: "运行与作业", testid: "nav-runs", Icon: IconHistory },
  { to: "/capabilities", label: "能力矩阵", testid: "nav-capabilities", Icon: IconLayers },
  { to: "/knowledge", label: "知识库", testid: "nav-knowledge", Icon: IconBook },
  { to: "/data", label: "数据中心", testid: "nav-data", Icon: IconBox },
  { to: "/memory", label: "记忆", testid: "nav-memory", Icon: IconBrain },
  { to: "/diagnostics", label: "系统诊断", testid: "nav-diagnostics", Icon: IconProbe },
  { to: "/settings", label: "系统设置", testid: "nav-settings", Icon: IconSettings },
];
