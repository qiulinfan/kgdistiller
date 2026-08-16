export interface ValidationError { instancePath: string; schemaPath: string; keyword: string; message?: string; }
export interface Validator { (value: unknown): boolean; errors?: ValidationError[] | null; }
export const apiResponse: Validator;
export const apiError: Validator;
export const recallRequest: Validator;
