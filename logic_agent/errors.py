from __future__ import annotations


class LogicAgentError(Exception):
    """Base class for errors that are safe to return to an MCP client."""

    code = "logic_agent_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        payload = {"ok": False, "error": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(LogicAgentError):
    code = "validation_error"


class NotFoundError(LogicAgentError):
    code = "not_found"


class BackendUnavailableError(LogicAgentError):
    code = "backend_unavailable"


class ConfigError(LogicAgentError):
    """A client config file exists but cannot be parsed or merged safely."""

    code = "config_error"



class UnsupportedFeatureError(LogicAgentError):
    code = "unsupported_feature"


class LimitExceededError(LogicAgentError):
    code = "limit_exceeded"


class CaptureError(LogicAgentError):
    code = "capture_error"

