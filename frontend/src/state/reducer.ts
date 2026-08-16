import type { ApiEnvelope, ApiResult, StatusResult, VaultSummary } from "../api/contracts";
import type { WorkspacePhase, WorkspaceState } from "./model";
import type { Route } from "./router";

export type WorkspaceAction =
  | { type: "navigate"; route: Route; navigation: number; refreshing: boolean }
  | { type: "bootstrap"; generation: string; status: StatusResult; vaults: VaultSummary[]; incomplete: WorkspaceState["incompleteVaults"]; epoch: number }
  | { type: "response"; response: ApiEnvelope<ApiResult>; related: ApiEnvelope<ApiResult>[]; notices: string[]; navigation: number }
  | { type: "phase"; phase: WorkspacePhase; message: string | null; clear: boolean };

export function reduceWorkspace(state: WorkspaceState, action: WorkspaceAction): WorkspaceState {
  switch (action.type) {
    case "navigate":
      return {
        ...state,
        route: action.route,
        navigation: action.navigation,
        phase: action.refreshing ? "refreshing" : "loading",
        response: action.refreshing ? state.response : null,
        related: action.refreshing ? state.related : [],
        routeNotices: action.refreshing ? state.routeNotices : [],
        message: null
      };
    case "bootstrap":
      return {
        ...state,
        generation: action.generation,
        status: action.status,
        vaults: action.vaults,
        incompleteVaults: action.incomplete,
        epoch: action.epoch,
        phase: action.incomplete.length ? "partial" : "ready",
        response: null,
        related: [],
        routeNotices: [],
        message: null
      };
    case "response":
      if (state.navigation !== action.navigation || state.generation !== action.response.generation) return state;
      return {
        ...state,
        response: action.response,
        related: action.related,
        routeNotices: action.notices,
        incompleteVaults: action.response.incomplete_vaults,
        phase: action.response.status === "partial" ? "partial" : "ready",
        message: null
      };
    case "phase":
      return {
        ...state,
        phase: action.phase,
        message: action.message,
        generation: action.clear ? null : state.generation,
        response: action.clear ? null : state.response,
        related: action.clear ? [] : state.related,
        routeNotices: action.clear ? [] : state.routeNotices,
        vaults: action.clear ? [] : state.vaults,
        incompleteVaults: action.clear ? [] : state.incompleteVaults,
        status: action.clear ? null : state.status
      };
  }
}
