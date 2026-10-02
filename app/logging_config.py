import logging
import traceback


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    # Telethon's own errors can embed RPC arguments or update objects. Our wrapper
    # reports exception types and stack locations without exception text or locals.
    logger = logging.getLogger("telethon")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False


def log_failure(context: str, error: BaseException) -> None:
    frames = traceback.extract_tb(error.__traceback__)
    locations = " > ".join(f"{frame.name}:{frame.lineno}" for frame in frames)
    logging.getLogger("app").error("%s: %s (%s)", context, type(error).__name__, locations)
