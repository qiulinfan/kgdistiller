import type { ApiEnvelope, ApiResult } from "../api/contracts";
import { ApiClient } from "../api/client";
import { ApiFault, ContractFailure, GenerationMismatch } from "../api/errors";
import { initialState, type WorkspaceState } from "./model";
import { reduceWorkspace } from "./reducer";
import { sameRoute, type Route } from "./router";

type Listener = (state: WorkspaceState) => void;
export interface LoadedRoute {
  primary: ApiEnvelope<ApiResult>;
  related: ApiEnvelope<ApiResult>[];
  notices?: string[];
}
type Loader = (generation: string, signal: AbortSignal) => Promise<ApiEnvelope<ApiResult> | LoadedRoute>;

export class GenerationStore {
  readonly #client: ApiClient;
  readonly #listeners = new Set<Listener>();
  #state: WorkspaceState = initialState;
  #sequence = 0;
  #epoch = 0;
  #abort: AbortController | null = null;

  constructor(client = new ApiClient()) {
    this.#client = client;
  }

  get state(): WorkspaceState { return this.#state; }

  subscribe(listener: Listener): () => void {
    this.#listeners.add(listener);
    listener(this.#state);
    return () => this.#listeners.delete(listener);
  }

  async navigate(route: Route, loader?: Loader): Promise<void> {
    const sequence = ++this.#sequence;
    this.#abort?.abort();
    const controller = new AbortController();
    this.#abort = controller;
    this.#dispatch({
      type: "navigate",
      route,
      navigation: sequence,
      refreshing: sameRoute(this.#state.route, route) && this.#state.response !== null
    });
    try {
      if (this.#state.generation === null) await this.#bootstrap(sequence, controller.signal);
      if (sequence !== this.#sequence || controller.signal.aborted) return;
      if (!loader) {
        this.#dispatch({ type: "phase", phase: this.#state.incompleteVaults.length ? "partial" : "ready", message: null, clear: false });
        return;
      }
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const generation = this.#state.generation;
        if (!generation) throw new ContractFailure("generation bootstrap did not complete");
        try {
          const loaded = await loader(generation, controller.signal);
          const response = "primary" in loaded ? loaded.primary : loaded;
          const related = "primary" in loaded ? loaded.related : [];
          if (related.some((item) => item.generation !== response.generation)) throw new GenerationMismatch();
          if (sequence === this.#sequence && !controller.signal.aborted) {
            this.#dispatch({ type: "response", response, related, notices: "primary" in loaded ? loaded.notices ?? [] : [], navigation: sequence });
          }
          return;
        } catch (error) {
          if (controller.signal.aborted || sequence !== this.#sequence) return;
          if (
            attempt === 0 && error instanceof ApiFault &&
            (error.status === 409 || error.status === 428) &&
            ["stale-generation", "generation-required"].includes(error.payload.error.code)
          ) {
            this.#invalidate("stale-generation", "The knowledge generation changed; reloading.", false);
            await this.#bootstrap(sequence, controller.signal);
            continue;
          }
          throw error;
        }
      }
    } catch (error) {
      if (controller.signal.aborted || sequence !== this.#sequence) return;
      if (error instanceof ContractFailure || error instanceof GenerationMismatch) {
        this.#invalidate("contract-error", error.message, true);
      } else if (error instanceof ApiFault) {
        this.#dispatch({ type: "phase", phase: "unavailable", message: error.payload.error.message, clear: false });
      } else {
        this.#dispatch({ type: "phase", phase: "unavailable", message: "The local knowledge service is unavailable.", clear: false });
      }
    }
  }

  async #bootstrap(sequence: number, signal: AbortSignal): Promise<void> {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const status = await this.#client.status(signal);
      const vaults = await this.#client.vaults(signal);
      if (status.generation !== vaults.generation) continue;
      if (sequence !== this.#sequence || signal.aborted) return;
      this.#epoch += 1;
      this.#dispatch({
        type: "bootstrap",
        generation: status.generation,
        status: status.result,
        vaults: vaults.result.vaults,
        incomplete: status.incomplete_vaults,
        epoch: this.#epoch
      });
      return;
    }
    throw new GenerationMismatch();
  }

  #invalidate(
    phase: "stale-generation" | "contract-error",
    message: string,
    abort: boolean
  ): void {
    if (abort) this.#abort?.abort();
    this.#client.clearGeneration();
    this.#epoch += 1;
    this.#dispatch({ type: "phase", phase, message, clear: true });
  }

  #dispatch(action: Parameters<typeof reduceWorkspace>[1]): void {
    this.#state = reduceWorkspace(this.#state, action);
    for (const listener of this.#listeners) listener(this.#state);
  }
}
