import { describe, expect, it } from "vitest";

import { formatDateTime, formatFileSize, formatScanJobStatus, truncateText } from "./displayFormat";

describe("display format helpers", () => {
  it("truncates text longer than 10 characters with three dots", () => {
    expect(truncateText("1234567890")).toBe("1234567890");
    expect(truncateText("12345678901")).toBe("1234567890...");
  });

  it("does not truncate when max length is zero", () => {
    expect(truncateText("12345678901", 0)).toBe("12345678901");
  });

  it("keeps the original text available separately for tooltips", () => {
    const value = 'a<b>&"中文特殊字符';

    expect(truncateText(value, 4)).toBe("a<b>...");
    expect(value).toBe('a<b>&"中文特殊字符');
  });

  it("truncates text by bytes for mixed Chinese and ASCII content", () => {
    expect(truncateText("中文ABCD", 8)).toBe("中文AB...");
    expect(truncateText("中文ABCD", 10)).toBe("中文ABCD");
  });

  it("keeps text at the 50 byte table display standard", () => {
    const value = "12345678901234567890123456789012345678901234567890";

    expect(truncateText(value, 50)).toBe(value);
    expect(truncateText(`${value}1`, 50)).toBe(`${value}...`);
  });

  it("formats file sizes into natural units", () => {
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(1536)).toBe("1.5 KB");
    expect(formatFileSize(1048576)).toBe("1 MB");
    expect(formatFileSize(1073741824)).toBe("1 GB");
  });

  it("formats dates without milliseconds or timezone", () => {
    expect(formatDateTime("2026-06-24T01:02:03.456Z")).toMatch(
      /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/,
    );
    expect(formatDateTime(null)).toBe("-");
  });

  it("formats scan job status into Chinese labels", () => {
    expect(formatScanJobStatus("pending")).toBe("等待中");
    expect(formatScanJobStatus("running")).toBe("扫描中");
    expect(formatScanJobStatus("completed")).toBe("已完成");
    expect(formatScanJobStatus("failed")).toBe("失败");
    expect(formatScanJobStatus("custom")).toBe("未知状态");
  });
});
