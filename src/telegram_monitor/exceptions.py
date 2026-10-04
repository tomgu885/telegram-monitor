"""Public error names and stable exit codes. Never include raw RPC exceptions."""

CODES = {
    "deployment_failed": 10,
    "deploy_timeout": 11,
    "deployment_already_running": 12,
    "environment_not_found": 20,
    "service_not_found": 21,
    "invalid_config": 22,
    "menu_not_found": 30,
    "button_not_found": 31,
    "unexpected_menu": 32,
    "action_not_allowed": 33,
    "telegram_connection_error": 40,
    "telegram_auth_error": 41,
    "telegram_rate_limit": 42,
    "session_busy": 43,
    "internal_error": 50,
}


class MonitorError(Exception):
    def __init__(self, error: str, **details):
        self.error = error
        self.code = CODES[error]
        self.details = details
        super().__init__(error)

    def payload(self) -> dict:
        return {"status": "error", "error": self.error, **self.details}
