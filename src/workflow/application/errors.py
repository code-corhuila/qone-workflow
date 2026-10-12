"""Errors of the application layer; the HTTP adapter translates them (403, 404) in one place."""


class Forbidden(Exception):
    def __init__(self, message: str = "this role may not perform the operation") -> None:
        super().__init__(message)
        self.message = message


class NotFound(Exception):
    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(message)
        self.message = message


class InvalidInput(Exception):
    """A field of the start request is invalid; `field` names it as the contract does."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message
