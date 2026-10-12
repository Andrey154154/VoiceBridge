"""Recoverable delivery outcomes; no dependency on a platform or GUI."""

class DeliveryDeferred(RuntimeError):
    """No paste shortcut was sent. Text is retained for explicit user retry."""
