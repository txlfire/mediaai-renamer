export function textByteLength(value: unknown): number {
  return new TextEncoder().encode(String(value ?? "")).length;
}

export function truncateText(value: unknown, maxLength = 10): string {
  const text = String(value ?? "");
  if (maxLength <= 0) {
    return text;
  }

  if (textByteLength(text) <= maxLength) {
    return text;
  }

  let currentBytes = 0;
  let result = "";

  for (const char of Array.from(text)) {
    const nextBytes = textByteLength(char);
    if (currentBytes + nextBytes > maxLength) {
      break;
    }
    currentBytes += nextBytes;
    result += char;
  }

  return `${result}...`;
}

export function formatFileSize(value: unknown): string {
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) {
    return "-";
  }

  const units = ["B", "KB", "MB", "GB", "TB"];
  let currentSize = size;
  let unitIndex = 0;

  while (currentSize >= 1024 && unitIndex < units.length - 1) {
    currentSize /= 1024;
    unitIndex += 1;
  }

  const formatted = Number.isInteger(currentSize)
    ? String(currentSize)
    : currentSize.toFixed(1).replace(/\.0$/, "");
  return `${formatted} ${units[unitIndex]}`;
}

export function formatDateTime(value: unknown): string {
  if (!value) {
    return "-";
  }

  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) {
    return "-";
  }

  const pad = (item: number) => String(item).padStart(2, "0");
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
  ].join(" ");
}

export function formatScanJobStatus(value: unknown): string {
  const status = String(value ?? "");
  const labels: Record<string, string> = {
    pending: "等待中",
    running: "扫描中",
    completed: "已完成",
    failed: "失败",
  };

  return labels[status] ?? status;
}
