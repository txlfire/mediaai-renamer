import { zhCnMessages as messages } from "../locales/zh-CN";

let generation = 0;

export function getSessionGeneration(): number {
  return generation;
}

export function invalidateSessionRequests(): void {
  generation += 1;
}

export class SessionChangedError extends Error {
  constructor() {
    super(messages.auth.sessionExpired);
    this.name = "SessionChangedError";
  }
}

export async function sessionRequest<T>(request: PromiseLike<T> | T, requestGeneration: number): Promise<T> {
  const result = await request;
  if (requestGeneration !== generation) throw new SessionChangedError();
  return result;
}
