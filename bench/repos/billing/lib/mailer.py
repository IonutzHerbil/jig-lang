"""Transactional email client."""


class MailError(Exception):
    """The message could not be sent."""


SENT: list[tuple[str, str, str]] = []


def send(to: str, subject: str, body: str) -> str:
    """Send an email; returns the message id."""
    if "@" not in to:
        raise MailError(f"invalid address {to}")
    SENT.append((to, subject, body))
    return f"msg_{len(SENT)}"
