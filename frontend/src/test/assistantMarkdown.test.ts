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

  it("keeps streaming output on a cheap plain-text render path", () => {
    render(
      React.createElement(StreamingContent, {
        text: "其他网络接口和地址如下： | 接口 | 状态 | IP 地址 | 用途 |\\n|---|---|---|---|\\n| `eth0` | UP | **10.0.8.4/22** | 主网卡、内网 IPv4 |",
      }),
    );

    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(document.querySelector(".streaming-plain-text")).toHaveTextContent("接口");
    expect(document.querySelector(".streaming-plain-text")).toHaveTextContent("10.0.8.4/22");
  });

  it("keeps br-separated weather details inside their original table cells", () => {
    const html = renderAssistantHtml(
      "| 城市 | 8/11 | 8/12 |\n|---|---|---|\n| **广州** | 阴/多云<br>27.6–35.4°C | 雷暴<br>27.6–37.0°C |\n| **深圳** | 阴/多云<br>27.4–34.7°C | 雷暴<br>28.4–35.0°C |",
    );

    const container = document.createElement("div");
    container.innerHTML = html;
    const rows = container.querySelectorAll("tbody tr");
    const firstRowCells = rows[0]?.querySelectorAll("td");

    expect(rows).toHaveLength(2);
    expect(firstRowCells).toHaveLength(3);
    expect(firstRowCells?.[0].textContent).toBe("广州");
    expect(firstRowCells?.[1].innerHTML).toContain("阴/多云<br>");
    expect(firstRowCells?.[1].textContent).toContain("27.6–35.4°C");
  });

  it("keeps consecutive city forecast tables separated", () => {
    const html = renderAssistantHtml([
      "### 广州",
      "| 日期 | 天气 | 气温 | 降水概率 | 累计降水 | 风速 |",
      "|---|---|---|---|---|---|",
      "| 08-11 | 多云 | 27–35 | 12% | 0.0 | 11.2 |",
      "| 08-12 | 雷暴 | 27–37 | 80% | 5.0 | 13.7 |",
      "",
      "### 深圳",
      "| 日期 | 天气 | 气温 | 降水概率 | 累计降水 | 风速 |",
      "|---|---|---|---|---|---|",
      "| 08-11 | 多云 | 27–34 | 8% | 0.0 | 13.8 |",
      "| 08-12 | 雷暴 | 28–35 | 62% | 0.6 | 15.4 |",
    ].join("\n"));

    const container = document.createElement("div");
    container.innerHTML = html;
    const tables = container.querySelectorAll("table");

    expect(tables).toHaveLength(2);
    expect(container.querySelectorAll("h3")).toHaveLength(2);
    expect(tables[0].querySelectorAll("tbody tr")).toHaveLength(2);
    expect(tables[1].querySelectorAll("tbody tr")).toHaveLength(2);
    expect(container.textContent).not.toContain("---");
  });

  it("allows only br while escaping other inline html", () => {
    const html = renderAssistantHtml("正常<br>换行 <img src=x onerror=alert(1)>");

    expect(html).toMatch(/正常<br\s*\/>换行/);
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).not.toContain("<img");
  });
});
