"""Logging: JSON lines in production, readable lines locally; every line carries
the request id of the request it belongs to, when there is one."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

_HANDLER_NAME = "zotero_rag_web"

#: The id of the request being handled, set by the request-id middleware.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def _add_request_id(
    logger: object, method: str, event: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    current = request_id.get()
    if current is not None:
        event.setdefault("request_id", current)
    return event


def configure(level: str = "INFO", fmt: str = "json") -> None:
    """Route the standard library's logging (uvicorn, the core) through structlog."""
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )
    handler.set_name(_HANDLER_NAME)
    root = logging.getLogger()
    # Replace only our own handler, so configuring twice (tests, reloads) neither
    # duplicates lines nor removes handlers someone else installed.
    root.handlers[:] = [h for h in root.handlers if h.get_name() != _HANDLER_NAME]
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn installs its own handlers; route its records through ours instead.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    # The app writes its own access line, with the request id. uvicorn logs access
    # whenever its logger reaches *any* handler, so it is cut off, not just unrouted.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
