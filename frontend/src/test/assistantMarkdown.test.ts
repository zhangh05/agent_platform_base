import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { StreamingContent } from "../pages/AgentWorkbench/components/StreamingContent";
import { renderAssistantHtml } from "../utils/displayText";

describe("assistant markdown rendering", () => {
  it("renders escaped network tables as real tables", () => {
    const html = renderAssistantHtml(
      "其他网络接口和地址如下： | 接口 | 状态 | IP 地址 | 用途 |\\n|---|---|---|---|\\n| `eth0` | UP | **10.0.8.4/22** | 主网卡、内网 IPv4 |\\n| `lo` | UP | `127.0.0.1/8` | 本地回环 |",
    );

    expect(html).toContain("<table>");
    expect(html).toContain("<th");
    expect(html).toContain("接口");
    expect(html).toContain("10.0.8.4/22");
    expect(html).not.toContain("\\n");
  });

  it("renders escaped network tables while streaming", () => {
    render(
      React.createElement(StreamingContent, {
        text: "其他网络接口和地址如下： | 接口 | 状态 | IP 地址 | 用途 |\\n|---|---|---|---|\\n| `eth0` | UP | **10.0.8.4/22** | 主网卡、内网 IPv4 |",
      }),
    );

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("接口")).toBeInTheDocument();
    expect(screen.getByText("10.0.8.4/22")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("\\n");
  });
});
