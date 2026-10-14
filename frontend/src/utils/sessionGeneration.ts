let generation = 0;

export function getSessionGeneration(): number {
  return generation;
}

export function invalidateSessionRequests(): void {
  generation += 1;
}

export class SessionChangedError extends Error {
  constructor() {
    super("Request belongs to a previous session");
    this.name = "SessionChangedError";
  }
}

export function isSessionChangedError(error: unknown): error is SessionChangedError {
  return error instanceof SessionChangedError;
}

export async function sessionRequest<T>(request: PromiseLike<T> | T, requestGeneration: number): Promise<T> {
  try {
    const result = await request;
    if (requestGeneration !== generation) throw new SessionChangedError();
    return result;
  } catch (error) {
    // 旧会话的失败同样需要隔离，不能覆盖新会话的状态或错误提示。
    if (requestGeneration !== generation) throw new SessionChangedError();
    throw error;
  }
}
