import type { ApiErrorPayload } from "./contracts";

export class ContractFailure extends Error {
  constructor(message = "API data violates the closed contract") {
    super(message);
    this.name = "ContractFailure";
  }
}

export class ApiFault extends Error {
  readonly status: number;
  readonly payload: ApiErrorPayload;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.error.message);
    this.name = "ApiFault";
    this.status = status;
    this.payload = payload;
  }
}

export class GenerationMismatch extends ContractFailure {
  constructor() {
    super("API response generation does not match the request epoch");
    this.name = "GenerationMismatch";
  }
}
