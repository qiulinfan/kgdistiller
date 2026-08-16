"""Small deterministic JSON Schema evaluator for kgdistiller's packaged contracts.

It implements only the Draft 2020-12 keywords used by this project. Keeping
the evaluator local lets source-vendored hosts validate the exact same schemas
without installing a second Python environment.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaViolation:
    path: tuple[str | int, ...]
    message: str


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _json_equality_fingerprint(value: Any) -> tuple[Any, ...]:
    """Return a hashable fingerprint with JSON Schema equality semantics."""

    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        # Python's numeric equality and hashes intentionally make 1 and 1.0
        # equal while the explicit tag keeps booleans distinct from numbers.
        if isinstance(value, float) and not math.isfinite(value):
            return ("non-finite-number", repr(value))
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(_json_equality_fingerprint(item) for item in value))
    if isinstance(value, dict):
        return (
            "object",
            tuple(
                (str(key), _json_equality_fingerprint(item))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ),
        )
    # Contract values originate as JSON, but retaining a tagged fallback keeps
    # this evaluator deterministic when called directly with an invalid value.
    return ("invalid", type(value).__qualname__, repr(value))


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"unsupported non-local JSON Schema reference: {reference}")
    current: Any = root
    for raw in reference[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"unresolved JSON Schema reference: {reference}")
        current = current[key]
    if not isinstance(current, dict):
        raise ValueError(f"JSON Schema reference is not an object: {reference}")
    return current


def validate_json_schema(instance: Any, schema: dict[str, Any]) -> list[SchemaViolation]:
    errors: list[SchemaViolation] = []

    def validate(value: Any, rule: dict[str, Any], path: tuple[str | int, ...]) -> None:
        if "$ref" in rule:
            validate(value, _resolve_ref(schema, str(rule["$ref"])), path)
            return
        if "const" in rule and value != rule["const"]:
            errors.append(SchemaViolation(path, f"must equal {rule['const']!r}"))
        if "enum" in rule and value not in rule["enum"]:
            errors.append(SchemaViolation(path, f"must be one of {rule['enum']!r}"))
        expected_type = rule.get("type")
        if expected_type is not None:
            expected_types = (
                [expected_type] if isinstance(expected_type, str) else list(expected_type)
            )
            if not any(_type_matches(value, item) for item in expected_types):
                errors.append(
                    SchemaViolation(path, f"must have type {' or '.join(expected_types)}")
                )
                return
        if "oneOf" in rule:
            matches = 0
            for branch in rule["oneOf"]:
                branch_errors = len(errors)
                validate(value, branch, path)
                if len(errors) == branch_errors:
                    matches += 1
                else:
                    del errors[branch_errors:]
            if matches != 1:
                errors.append(SchemaViolation(path, "must match exactly one oneOf branch"))
        if "anyOf" in rule:
            matched = False
            for branch in rule["anyOf"]:
                branch_errors = len(errors)
                validate(value, branch, path)
                if len(errors) == branch_errors:
                    matched = True
                    break
                del errors[branch_errors:]
            if not matched:
                errors.append(SchemaViolation(path, "must match at least one anyOf branch"))
        for branch in rule.get("allOf", []):
            validate(value, branch, path)
        if "not" in rule:
            branch_errors = len(errors)
            validate(value, rule["not"], path)
            matched = len(errors) == branch_errors
            del errors[branch_errors:]
            if matched:
                errors.append(SchemaViolation(path, "must not match the forbidden schema"))
        if "if" in rule:
            branch_errors = len(errors)
            validate(value, rule["if"], path)
            matched = len(errors) == branch_errors
            del errors[branch_errors:]
            selected = rule.get("then") if matched else rule.get("else")
            if isinstance(selected, dict):
                validate(value, selected, path)
        if isinstance(value, dict):
            if "minProperties" in rule and len(value) < int(rule["minProperties"]):
                errors.append(
                    SchemaViolation(
                        path, f"must contain at least {rule['minProperties']} properties"
                    )
                )
            if "maxProperties" in rule and len(value) > int(rule["maxProperties"]):
                errors.append(
                    SchemaViolation(
                        path, f"must contain at most {rule['maxProperties']} properties"
                    )
                )
            for required in rule.get("required", []):
                if required not in value:
                    errors.append(
                        SchemaViolation(path, f"is missing required property {required!r}")
                    )
            properties = rule.get("properties") or {}
            for key, child in properties.items():
                if key in value:
                    validate(value[key], child, (*path, key))
            additional = rule.get("additionalProperties", True)
            for key in sorted(set(value) - set(properties)):
                if additional is False:
                    errors.append(
                        SchemaViolation((*path, key), "is an unknown property")
                    )
                elif isinstance(additional, dict):
                    validate(value[key], additional, (*path, key))
            property_names = rule.get("propertyNames")
            if isinstance(property_names, dict):
                for key in sorted(value):
                    validate(key, property_names, (*path, key))
        if isinstance(value, list):
            if "minItems" in rule and len(value) < int(rule["minItems"]):
                errors.append(
                    SchemaViolation(path, f"must contain at least {rule['minItems']} items")
                )
            if "maxItems" in rule and len(value) > int(rule["maxItems"]):
                errors.append(
                    SchemaViolation(path, f"must contain at most {rule['maxItems']} items")
                )
            if rule.get("uniqueItems") is True:
                seen_items: set[tuple[Any, ...]] = set()
                for index, item in enumerate(value):
                    fingerprint = _json_equality_fingerprint(item)
                    if fingerprint in seen_items:
                        errors.append(
                            SchemaViolation(
                                (*path, index), "must not duplicate an earlier item"
                            )
                        )
                    else:
                        seen_items.add(fingerprint)
            item_rule = rule.get("items")
            if isinstance(item_rule, dict):
                for index, item in enumerate(value):
                    validate(item, item_rule, (*path, index))
            contains = rule.get("contains")
            if isinstance(contains, dict):
                matched = False
                for item in value:
                    branch_errors = len(errors)
                    validate(item, contains, path)
                    if len(errors) == branch_errors:
                        matched = True
                        break
                    del errors[branch_errors:]
                if not matched:
                    errors.append(SchemaViolation(path, "does not contain a required item"))
        if isinstance(value, str):
            if "minLength" in rule and len(value) < int(rule["minLength"]):
                errors.append(
                    SchemaViolation(path, f"must contain at least {rule['minLength']} characters")
                )
            if "maxLength" in rule and len(value) > int(rule["maxLength"]):
                errors.append(
                    SchemaViolation(path, f"must contain at most {rule['maxLength']} characters")
                )
            if "pattern" in rule and re.search(str(rule["pattern"]), value) is None:
                errors.append(
                    SchemaViolation(path, f"must match pattern {rule['pattern']!r}")
                )
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and "minimum" in rule
            and value < rule["minimum"]
        ):
            errors.append(SchemaViolation(path, f"must be >= {rule['minimum']}"))
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
            and "maximum" in rule
            and value > rule["maximum"]
        ):
            errors.append(SchemaViolation(path, f"must be <= {rule['maximum']}"))

    validate(instance, schema, ())
    return errors
