// Every call to main goes through here. Errors come as main sends them:
// {"error": {"code", "message", "details"}}.

import { authHeaders, baseUrl, loadConnection, type Connection } from "@/api/connection";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let connection: Connection = loadConnection();

export function currentConnection(): Connection {
  return connection;
}

export function setConnection(next: Connection): void {
  connection = next;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;

  let response: Response;
  try {
    response = await fetch(`${baseUrl(connection)}${path}`, {
      ...rest,
      headers: {
        ...authHeaders(connection),
        ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
        ...headers,
      },
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch (e) {
    throw new ApiError(0, "UNREACHABLE", "Агент не отвечает: он не запущен или адрес неверный", e);
  }

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const error = body?.error;
    throw new ApiError(response.status, error?.code ?? "ERROR", error?.message ?? `HTTP ${response.status}`, error?.details);
  }

  return body as T;
}
