"""Domain-level errors for game services (mapped to HTTP in controllers)."""


class UnitStateValidationError(ValueError):
    """Invalid unit state transition or constraint (e.g. attached hero activation)."""

    pass
