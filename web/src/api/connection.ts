// Where the API is. By default the dev server's proxy (/api), which adds the token itself.
// A browser without that proxy (another computer, a build served elsewhere) connects
// directly: main's address and token, kept in this browser only.

export type Connection = { kind: "proxy" } | { kind: "direct"; url: string; token: string };

const KEY = "agent.connection";

export function loadConnection(): Connection {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (saved?.kind === "direct" && typeof saved.url === "string" && typeof saved.token === "string") return saved;
  } catch {
    // Unreadable or blocked storage: the proxy is the default.
  }

  return { kind: "proxy" };
}

export function saveConnection(connection: Connection): void {
  try {
    if (connection.kind === "proxy") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, JSON.stringify(connection));
  } catch {
    // Without storage the connection lasts until the page reloads.
  }
}

export function baseUrl(connection: Connection): string {
  return connection.kind === "proxy" ? "/api" : connection.url.replace(/\/+$/, "");
}

export function authHeaders(connection: Connection): Record<string, string> {
  return connection.kind === "direct" ? { Authorization: `Bearer ${connection.token}` } : {};
}
