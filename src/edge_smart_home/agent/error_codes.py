"""Stable internal intent codes; wire Schema and human messages stay separate.

These are project codes, not HTTP status codes or an RFC 9457 endpoint. Callers
attach a code at an observed validation/transport/policy stage. The helpers do
not inspect human-readable exception messages or reinterpret model responses.
"""
from __future__ import annotations

from enum import Enum, unique


@unique
class IntentErrorCode(str, Enum):
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    SCHEDULING_DEFERRED = "SCHEDULING_DEFERRED"
    REQUEST_INPUT_INVALID = "REQUEST_INPUT_INVALID"
    REQUEST_ARGUMENT_MISSING = "REQUEST_ARGUMENT_MISSING"
    REQUEST_ARGUMENT_INVALID = "REQUEST_ARGUMENT_INVALID"
    REQUEST_TARGET_AMBIGUOUS = "REQUEST_TARGET_AMBIGUOUS"
    REQUEST_PLAN_MISMATCH = "REQUEST_PLAN_MISMATCH"
    QUERY_PLAN_REQUIRED = "QUERY_PLAN_REQUIRED"
    JSON_SYNTAX = "JSON_SYNTAX"
    JSON_ENCODING = "JSON_ENCODING"
    JSON_DUPLICATE_KEY = "JSON_DUPLICATE_KEY"
    JSON_NONFINITE = "JSON_NONFINITE"
    JSON_ROOT_TYPE = "JSON_ROOT_TYPE"
    JSON_INPUT_TYPE = "JSON_INPUT_TYPE"
    WIRE_CONTRACT_INVALID = "WIRE_CONTRACT_INVALID"
    UNKNOWN_DEVICE = "UNKNOWN_DEVICE"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    PLAN_VALIDATION_FAILED = "PLAN_VALIDATION_FAILED"
    MODEL_HTTP_ERROR = "MODEL_HTTP_ERROR"
    MODEL_TRANSPORT_ERROR = "MODEL_TRANSPORT_ERROR"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_OUTER_JSON_INVALID = "MODEL_OUTER_JSON_INVALID"
    MODEL_RESPONSE_INCOMPLETE = "MODEL_RESPONSE_INCOMPLETE"
    MODEL_RESPONSE_TRUNCATED = "MODEL_RESPONSE_TRUNCATED"
    MODEL_RESPONSE_EMPTY = "MODEL_RESPONSE_EMPTY"
    MODEL_IDENTITY_MISMATCH = "MODEL_IDENTITY_MISMATCH"
    MODEL_THINKING_POLICY = "MODEL_THINKING_POLICY"
    MODEL_SERVER_ERROR = "MODEL_SERVER_ERROR"
    STATE_CONTEXT_UNAVAILABLE = "STATE_CONTEXT_UNAVAILABLE"
    ROUTE_UNSUPPORTED = "ROUTE_UNSUPPORTED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CONFIRMATION_CANCELLED = "CONFIRMATION_CANCELLED"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_PARTIAL_FAILURE = "EXECUTION_PARTIAL_FAILURE"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"
    INTENT_INTERNAL_ERROR = "INTENT_INTERNAL_ERROR"
    UNCLASSIFIED_ERROR = "UNCLASSIFIED_ERROR"


MAX_INTENT_INPUT_CHARS = 512


def input_error_code(text: object) -> str | None:
    """Check the existing text boundary before truncation, routing or a model.

    The bound is in Unicode characters, matching the existing prompt boundary;
    oversized input is rejected rather than silently interpreted as a prefix.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_INTENT_INPUT_CHARS:
        return IntentErrorCode.REQUEST_INPUT_INVALID.value
    return None


def normalize_error_code(value: IntentErrorCode | str | None) -> str | None:
    """Return a checked wire-friendly string; null means no reported error.

    Unknown explicit codes are rejected instead of silently becoming success.
    The spelling/value is stable independently of translations and messages.
    """
    if value is None:
        return None
    if not isinstance(value, (str, IntentErrorCode)):
        raise ValueError("Intent error code must be a known string or null")
    try:
        return IntentErrorCode(value).value
    except ValueError as error:
        raise ValueError("Unknown intent error code") from error


_KIND_DEFAULTS = {
    "plan": None,
    "response": None,
    "clarification": IntentErrorCode.CLARIFICATION_REQUIRED,
    "rejected": IntentErrorCode.REQUEST_REJECTED,
    "unavailable": IntentErrorCode.CAPABILITY_UNAVAILABLE,
}


def result_error_code(kind: str, explicit: IntentErrorCode | str | None = None) -> str | None:
    """Give an internal result its explicit code or broad kind default.

    A valid query plan has no error code. QUERY_PLAN_REQUIRED may separately be
    a successful consistency-rewrite diagnostic; do not set it on that plan.
    This helper does not certify plan validity, execution or physical state.
    """
    if explicit is not None:
        return normalize_error_code(explicit)
    return normalize_error_code(_KIND_DEFAULTS.get(kind, IntentErrorCode.UNCLASSIFIED_ERROR))


_STATUS_DEFAULTS = {
    "executed": None,
    "responded": None,
    "clarification_required": IntentErrorCode.CLARIFICATION_REQUIRED,
    "request_rejected": IntentErrorCode.REQUEST_REJECTED,
    "needs_intelligence": IntentErrorCode.CAPABILITY_UNAVAILABLE,
    "unavailable": IntentErrorCode.CAPABILITY_UNAVAILABLE,
    "plan_rejected": IntentErrorCode.PLAN_VALIDATION_FAILED,
    "not_routed": IntentErrorCode.ROUTE_UNSUPPORTED,
    "confirmation_required": IntentErrorCode.CONFIRMATION_REQUIRED,
    "confirmation_cancelled": IntentErrorCode.CONFIRMATION_CANCELLED,
    "confirmation_expired": IntentErrorCode.CONFIRMATION_EXPIRED,
    "device_error": IntentErrorCode.EXECUTION_FAILED,
    "partial_failure": IntentErrorCode.EXECUTION_PARTIAL_FAILURE,
    "duplicate_suppressed": IntentErrorCode.DUPLICATE_SUPPRESSED,
}


def assistant_error_code(
    status: str, explicit: IntentErrorCode | str | None = None,
    result_code: IntentErrorCode | str | None = None,
) -> str | None:
    """Resolve explicit app code, structured result code, then exact status.

    Do not substitute a generic unavailable code for a known transport failure.
    Unknown statuses remain visibly unclassified, rather than reported OK.
    Existing status/kind/message/reason remain the authoritative outcome fields.
    """
    if explicit is not None:
        return normalize_error_code(explicit)
    if result_code is not None:
        return normalize_error_code(result_code)
    return normalize_error_code(_STATUS_DEFAULTS.get(status, IntentErrorCode.UNCLASSIFIED_ERROR))


_DIAGNOSTIC_DEFAULTS = {
    "ok": None,
    "model_json_syntax": IntentErrorCode.JSON_SYNTAX,
    "model_json_encoding": IntentErrorCode.JSON_ENCODING,
    "model_json_duplicate_key": IntentErrorCode.JSON_DUPLICATE_KEY,
    "model_json_nonfinite": IntentErrorCode.JSON_NONFINITE,
    "model_json_root_type": IntentErrorCode.JSON_ROOT_TYPE,
    "model_json_input_type": IntentErrorCode.JSON_INPUT_TYPE,
    "wire_contract": IntentErrorCode.WIRE_CONTRACT_INVALID,
    "plan_validation": IntentErrorCode.PLAN_VALIDATION_FAILED,
    "transport_error": IntentErrorCode.MODEL_TRANSPORT_ERROR,
    "transport_http_error": IntentErrorCode.MODEL_HTTP_ERROR,
    "transport_timeout": IntentErrorCode.MODEL_TIMEOUT,
    "outer_json_decode": IntentErrorCode.MODEL_OUTER_JSON_INVALID,
    "outer_json_type": IntentErrorCode.MODEL_OUTER_JSON_INVALID,
    "generation_incomplete": IntentErrorCode.MODEL_RESPONSE_INCOMPLETE,
    "generation_truncated": IntentErrorCode.MODEL_RESPONSE_TRUNCATED,
    "empty_model_response": IntentErrorCode.MODEL_RESPONSE_EMPTY,
    "model_identity": IntentErrorCode.MODEL_IDENTITY_MISMATCH,
    "thinking_policy_violation": IntentErrorCode.MODEL_THINKING_POLICY,
    "server_error": IntentErrorCode.MODEL_SERVER_ERROR,
    "request_guard_error": IntentErrorCode.INTENT_INTERNAL_ERROR,
    "semantic_mismatch": IntentErrorCode.REQUEST_PLAN_MISMATCH,
    "unknown_device": IntentErrorCode.UNKNOWN_DEVICE,
    "unsupported_action": IntentErrorCode.UNSUPPORTED_ACTION,
    "invalid_arguments": IntentErrorCode.INVALID_ARGUMENTS,
    "request_argument_missing": IntentErrorCode.REQUEST_ARGUMENT_MISSING,
    "request_input_invalid": IntentErrorCode.REQUEST_INPUT_INVALID,
    "request_argument_invalid": IntentErrorCode.REQUEST_ARGUMENT_INVALID,
    "request_target_ambiguous": IntentErrorCode.REQUEST_TARGET_AMBIGUOUS,
    "request_plan_mismatch": IntentErrorCode.REQUEST_PLAN_MISMATCH,
    "query_plan_required": IntentErrorCode.QUERY_PLAN_REQUIRED,
}


def diagnostic_error_code(
    category: str, explicit: IntentErrorCode | str | None = None,
) -> str | None:
    """Map an already classified stage/category; never parse English details.

    A typed exception or guard decision may provide a more specific explicit
    code. A bare legacy plan_validation category only proves that broad stage;
    it cannot be split into device/action/argument errors from its prose.
    """
    if explicit is not None:
        return normalize_error_code(explicit)
    return normalize_error_code(_DIAGNOSTIC_DEFAULTS.get(category, IntentErrorCode.UNCLASSIFIED_ERROR))
