import logging
import sys

logger = logging.getLogger("dev-portal")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
if not logger.handlers:
    logger.addHandler(_handler)


def _one_line(value: str, limit: int) -> str:
    # Newlines would let one event forge a second audit line.
    cleaned = "".join(ch if ch.isprintable() and ch not in "\r\n" else " " for ch in value)
    return cleaned[:limit]


def audit_log(event: str, username: str, detail: str = "") -> None:
    """Structured audit trail for auth/deployment events; always goes to stdout (container logs)."""
    logger.info(
        "AUDIT event=%s username=%s detail=%s",
        _one_line(event, 80),
        _one_line(username, 128),
        _one_line(detail, 500),
    )
