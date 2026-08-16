import type { ApiEnvelope, ApiResult, IncompleteVault, StatusResult, VaultSummary } from "../api/contracts";
import type { Route } from "./router";

export type WorkspacePhase =
  | "booting"
  | "loading"
  | "ready"
  | "partial"
  | "refreshing"
  | "stale-generation"
  | "unavailable"
  | "contract-error";

export interface WorkspaceState {
  phase: WorkspacePhase;
  route: Route;
  generation: string | null;
  epoch: number;
  navigation: number;
  status: StatusResult | null;
  vaults: VaultSummary[];
  incompleteVaults: IncompleteVault[];
  response: ApiEnvelope<ApiResult> | null;
  related: ApiEnvelope<ApiResult>[];
  routeNotices: string[];
  message: string | null;
}

export const initialState: WorkspaceState = {
  phase: "booting",
  route: { name: "home" },
  generation: null,
  epoch: 0,
  navigation: 0,
  status: null,
  vaults: [],
  incompleteVaults: [],
  response: null,
  related: [],
  routeNotices: [],
  message: null
};
