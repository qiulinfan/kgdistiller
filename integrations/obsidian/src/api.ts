/** Direct JSON requests from the desktop plugin, with reusable connections and cancellation. */
import { Agent as HttpAgent, request as httpRequest, type ClientRequest } from "http";
import { Agent as HttpsAgent, request as httpsRequest } from "https";

export type PostJson = (url: string, key: string, body: Record<string, unknown>, timeout: number) => Promise<unknown>;

export function apiUrl(value: string): URL {
  let url: URL;
  try { url = new URL(value.trim()); } catch { throw new Error("Set a complete API endpoint URL in the kgdistiller settings."); }
  if (url.username || url.password || url.hash || url.search) throw new Error("API endpoints must not contain credentials, query parameters or fragments.");
  if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) {
    throw new Error("Use HTTPS for an API endpoint, or HTTP on localhost.");
  }
  return url;
}

export class ApiClient {
  private readonly http = new HttpAgent({ keepAlive: true });
  private readonly https = new HttpsAgent({ keepAlive: true });
  private readonly pending = new Map<ClientRequest, (error: Error) => void>();
  private closed = false;

  readonly post: PostJson = async (endpoint, key, body, timeout) => {
    if (this.closed) throw new Error("The plugin's API client is closed.");
    const url = apiUrl(endpoint);
    const payload = JSON.stringify(body);
    return new Promise((resolve, reject) => {
      const secure = url.protocol === "https:";
      const request = (secure ? httpsRequest : httpRequest)(url, {
        method: "POST", agent: secure ? this.https : this.http,
        headers: {
          "Content-Type": "application/json", "Accept": "application/json",
          "Content-Length": Buffer.byteLength(payload),
          ...(key.trim() ? { Authorization: `Bearer ${key.trim()}` } : {}),
        },
      });
      let settled = false;
      const finish = (error?: Error, value?: unknown): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        this.pending.delete(request);
        if (error) reject(error); else resolve(value);
      };
      const abort = (error: Error): void => { finish(error); request.destroy(); };
      const timer = setTimeout(() => abort(new Error(`API request timed out after ${timeout / 1000} s.`)), timeout);
      this.pending.set(request, abort);
      // No redirects: credentials are sent only to the configured endpoint.
      request.on("response", (response) => {
        const status = response.statusCode ?? 0;
        if (status < 200 || status >= 300) {
          finish(new Error(`API request failed (HTTP ${status}).${status === 401 || status === 403 ? " Check the API key and access permissions." : ""}`));
          response.destroy();
          return;
        }
        const chunks: Buffer[] = [];
        let bytes = 0;
        response.on("data", (chunk: Buffer) => {
          bytes += chunk.length;
          if (bytes > 8 * 1024 * 1024) abort(new Error("API response exceeds 8 MiB."));
          else chunks.push(chunk);
        });
        response.on("error", () => finish(new Error("API response was interrupted.")));
        response.on("end", () => {
          try { finish(undefined, JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
          catch { finish(new Error("The API returned invalid JSON.")); }
        });
      });
      request.on("error", (error) => {
        // Transport errors can include headers or URLs; expose only our own errors and error codes.
        finish(new Error(`API connection failed (${(error as NodeJS.ErrnoException).code ?? "network error"}).`));
      });
      request.end(payload);
    });
  };

  cancel(): void {
    for (const abort of this.pending.values()) abort(new Error("API request cancelled."));
  }

  close(): void {
    this.closed = true;
    this.cancel();
    this.http.destroy();
    this.https.destroy();
  }
}
