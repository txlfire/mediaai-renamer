// 静默请求与手动刷新共用互斥；切换任务后旧响应不能更新当前页面。
export function createScopedRequestGate() {
  let sequence = 0;
  let activeToken: number | null = null;
  return {
    begin(): number | null {
      if (activeToken !== null) return null;
      activeToken = ++sequence;
      return activeToken;
    },
    isCurrent(token: number): boolean {
      return token === activeToken;
    },
    finish(token: number): boolean {
      if (token !== activeToken) return false;
      activeToken = null;
      return true;
    },
    invalidate(): void {
      activeToken = null;
    },
  };
}
