import argparse
import asyncio
import fcntl
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from app.config import ConfigError, load_settings
from app.logging_config import log_failure, setup_logging
from app.storage import Storage
from app.telegram_client import AuthenticationFailure, run


@contextmanager
def instance_lock(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    data_dir.chmod(0o700)
    with (data_dir / "recorder.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigError("此 data 目录已有 recorder 运行，请勿启动第二个实例。") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main() -> int:
    os.umask(0o077)
    setup_logging()
    parser = argparse.ArgumentParser(description="Local Telegram recorder with opt-in screenshots")
    parser.add_argument("--check-config", action="store_true", help="只检查配置，不连接 Telegram")
    args = parser.parse_args()
    if sys.version_info < (3, 12):  # noqa: UP036 - give older interpreters a clear startup error
        logging.error("需要 Python 3.12+。")
        return 1
    result = 0
    try:
        settings = load_settings()
        if args.check_config:
            logging.info("Configuration valid (credentials not checked with Telegram).")
            return 0
        with instance_lock(settings.data_dir):
            storage = Storage(settings.data_dir)
            try:
                asyncio.run(run(settings, storage))
            except KeyboardInterrupt:
                logging.info("Stopping recorder...")
            finally:
                try:
                    while storage.flush_archive():
                        pass
                except Exception as exc:
                    log_failure(
                        "Final archive flush failed; pending events retained in SQLite", exc
                    )
                    result = 1
                finally:
                    storage.close()
                    logging.info("SQLite closed; recorder stopped.")
    except (ConfigError, AuthenticationFailure) as exc:
        logging.error("%s", exc)
        result = 1
    except Exception as exc:
        log_failure("Recorder stopped due to an unrecoverable error", exc)
        result = 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
