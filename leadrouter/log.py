"""One way to print: every message goes through `redact`, so a secret can never reach the terminal."""

from . import settings


def log(message: object = "") -> None:
    print(settings.redact(message))
