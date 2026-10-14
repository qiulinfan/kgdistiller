/** Direct JSON requests from the desktop plugin, with reusable connections and cancellation. */
import { Agent as HttpAgent, request as httpRequest, type ClientRequest } from "http";
import { Agent as HttpsAgent, request as httpsRequest } from "https";

export type PostJson = (url: string, key: string, body: Record<string, unknown>, timeout: number) => Promise<unknown>;
/** POST a JSON body with the caller's own headers and return the response text. */
export type PostText = (url: string, headers: Record<string, string>, body: Record<string, unknown>, timeout: number) => Promise<string>;

/** A failed API request; `status` is the HTTP status when the service answered. */
export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
  }
}

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

  /** POST JSON with a bearer key and parse the JSON reply. */
  readonly post: PostJson = async (endpoint, key, body, timeout) => {
    const text = await this.postText(endpoint, {
      Accept: "application/json",
      ...(key.trim() ? { Authorization: `Bearer ${key.trim()}` } : {}),
    }, body, timeout);
    try { return JSON.parse(text); } catch { throw new ApiError("The API returned invalid JSON."); }
  };

  readonly postText: PostText = async (endpoint, headers, body, timeout) => {
    if (this.closed) throw new ApiError("The plugin's API client is closed.");
    const url = apiUrl(endpoint);
    const payload = JSON.stringify(body);
    return new Promise((resolve, reject) => {
      const secure = url.protocol === "https:";
      const request = (secure ? httpsRequest : httpRequest)(url, {
        method: "POST", agent: secure ? this.https : this.http,
        headers: { ...headers, "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) },
      });
      let settled = false;
      const finish = (error?: Error, value?: string): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        this.pending.delete(request);
        if (error) reject(error); else resolve(value ?? "");
      };
      const abort = (error: Error): void => { finish(error); request.destroy(); };
      const timer = setTimeout(() => abort(new ApiError(`API request timed out after ${timeout / 1000} s.`)), timeout);
      this.pending.set(request, abort);
      // No redirects: credentials are sent only to the configured endpoint.
      request.on("response", (response) => {
        const status = response.statusCode ?? 0;
        if (status < 200 || status >= 300) {
          finish(new ApiError(`API request failed (HTTP ${status}).${status === 401 || status === 403 ? " Check the API key and access permissions." : ""}`, status));
          response.destroy();
          return;
        }
        const chunks: Buffer[] = [];
        let bytes = 0;
        response.on("data", (chunk: Buffer) => {
          bytes += chunk.length;
          if (bytes > 8 * 1024 * 1024) abort(new ApiError("API response exceeds 8 MiB."));
          else chunks.push(chunk);
        });
        response.on("error", () => finish(new ApiError("API response was interrupted.")));
        response.on("end", () => finish(undefined, Buffer.concat(chunks).toString("utf8")));
      });
      request.on("error", (error) => {
        // Transport errors can include headers or URLs; expose only our own errors and error codes.
        finish(new ApiError(`API connection failed (${(error as NodeJS.ErrnoException).code ?? "network error"}).`));
      });
      request.end(payload);
    });
  };

  cancel(): void {
    for (const abort of this.pending.values()) abort(new ApiError("API request cancelled."));
  }

  close(): void {
    this.closed = true;
    this.cancel();
    this.http.destroy();
    this.https.destroy();
  }
}
