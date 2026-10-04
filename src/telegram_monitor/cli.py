"""Public command interface: JSON on stdout, diagnostics and prompts on stderr."""

import asyncio
import json
import logging
import signal
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from telethon import errors

from telegram_monitor.config import ButtonMatch, Configuration
from telegram_monitor.exceptions import MonitorError
from telegram_monitor.menu import Menu
from telegram_monitor.state import CursorStore
from telegram_monitor.telegram_client import TelethonMenuClient, connect, login

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
auth = typer.Typer(no_args_is_help=True)
menu = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
app.add_typer(auth, name="auth")
app.add_typer(menu, name="menu")
app.add_typer(config_app, name="config")

Env = Annotated[str, typer.Option("--env", help="Environment config name")]
OptionalEnv = Annotated[str | None, typer.Option("--env")]
Json = Annotated[bool, typer.Option("--json", help="JSON output (also the default)")]
MessageId = Annotated[int | None, typer.Option("--message-id")]


def emit(payload):
    typer.echo(json.dumps(payload, ensure_ascii=False, allow_nan=False))


@app.callback()
def options(
    ctx: typer.Context,
    config: Annotated[Path, typer.Option("--config")] = Path("config/telegram.yaml"),
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
):
    ctx.obj = config
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        force=True,
    )
    # Library exception messages may contain RPC parameters or authentication data.
    logging.getLogger("telethon").disabled = True
    logging.getLogger("telethon").setLevel(logging.CRITICAL + 1)


async def interruptible(operation):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        return await operation()
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def execute(operation):
    try:
        result = asyncio.run(interruptible(operation))
        if result is not None:
            emit(result)
        return
    except MonitorError as exc:
        error = exc
    except errors.FloodWaitError as exc:
        error = MonitorError("telegram_rate_limit", retry_after_seconds=exc.seconds)
    except (
        errors.UnauthorizedError,
        errors.AuthKeyError,
        errors.AuthKeyDuplicatedError,
        errors.ApiIdInvalidError,
        errors.PhoneNumberInvalidError,
        errors.PhoneNumberBannedError,
        errors.PhoneCodeExpiredError,
        errors.PasswordHashInvalidError,
    ):
        error = MonitorError("telegram_auth_error")
    except (ConnectionError, TimeoutError, errors.RPCError):
        error = MonitorError("telegram_connection_error")
    except ValidationError:
        error = MonitorError("invalid_config", reason="invalid_command_options")
    except (KeyboardInterrupt, asyncio.CancelledError):
        emit({"status": "interrupted", "error": "interrupted"})
        raise typer.Exit(130) from None
    except Exception as exc:
        logging.getLogger(__name__).error("Operation failed (%s)", type(exc).__name__)
        error = MonitorError("internal_error")
    emit(error.payload())
    raise typer.Exit(error.code)


def cursor(config: Configuration) -> CursorStore:
    return CursorStore(config.state_dir, f"{config.path}:{config.session}")


@auth.command("login")
def auth_login(ctx: typer.Context, json_output: Json = False):
    async def run():
        conf = Configuration(ctx.obj)
        async with connect(conf, require_auth=False) as client:
            await login(client)
            return {"status": "ok", "authorized": True}

    execute(run)


@auth.command("status")
def auth_status(ctx: typer.Context, json_output: Json = False):
    async def run():
        conf = Configuration(ctx.obj)
        async with connect(conf, require_auth=False) as client:
            authorized = await client.is_user_authorized()
            if not authorized:
                raise MonitorError("telegram_auth_error", authorized=False, reason="run_auth_login")
            return {"status": "ok", "authorized": True}

    execute(run)


@config_app.command("validate")
def validate(ctx: typer.Context, env: OptionalEnv = None, json_output: Json = False):
    async def run():
        conf = Configuration(ctx.obj)
        names = (
            [env]
            if env
            else sorted(
                path.stem
                for path in conf.path.parent.glob("*.yaml")
                if path != conf.path and not path.name.endswith(".example.yaml")
            )
        )
        issues = []
        for name in names:
            try:
                conf.environment(name)
            except MonitorError as exc:
                issues.append({"environment": name, **exc.payload()})
        if issues:
            raise MonitorError("invalid_config", issues=issues)
        return {"status": "ok", "environments": names, "credentials_checked": False}

    execute(run)


@app.command()
def inspect(
    ctx: typer.Context,
    chat: Annotated[str, typer.Option("--chat")] = "deployment",
    limit: Annotated[int, typer.Option(min=1, max=100)] = 10,
    json_output: Json = False,
):
    async def run():
        conf = Configuration(ctx.obj)
        target = conf.target(chat)
        async with connect(conf) as client:
            adapter = TelethonMenuClient(client, target)
            await adapter.initialize()
            messages = await client.get_messages(adapter.peer, limit=limit, from_user=adapter.bot)
            snapshots = [await adapter.get_message(message.id) for message in messages]
            return {
                "status": "ok",
                "chat_id": target.chat_id,
                "messages": [message.payload() for message in snapshots],
            }

    execute(run)


@app.command()
def watch(
    ctx: typer.Context,
    chat: Annotated[str, typer.Option("--chat")] = "deployment",
    sender: Annotated[str | None, typer.Option("--sender")] = None,
    json_lines: Annotated[bool, typer.Option("--json-lines")] = False,
):
    async def run():
        conf = Configuration(ctx.obj)
        async with connect(conf) as client:
            adapter = TelethonMenuClient(client, conf.target(chat))
            await adapter.initialize()
            await adapter.watch(emit, sender)

    execute(run)


def menu_operation(ctx, env, operation, **kwargs):
    async def run():
        conf = Configuration(ctx.obj)
        store = cursor(conf)
        selected = env if env is not None else store.read()["environment"]
        environment = conf.environment(selected)
        target = conf.target(environment.target)
        async with connect(conf) as client:
            adapter = TelethonMenuClient(client, target)
            await adapter.initialize()
            service = Menu(
                adapter, environment, target, store, conf.settings.defaults.message_timeout_seconds
            )
            return await getattr(service, operation)(**kwargs)

    execute(run)


@menu.command("open")
def menu_open(ctx: typer.Context, env: Env, json_output: Json = False):
    menu_operation(ctx, env, "open")


@menu.command("reset")
def menu_reset(ctx: typer.Context, env: Env, json_output: Json = False):
    menu_operation(ctx, env, "open")


@menu.command("buttons")
def menu_buttons(
    ctx: typer.Context,
    env: OptionalEnv = None,
    message_id: MessageId = None,
    json_output: Json = False,
):
    menu_operation(ctx, env, "buttons", message_id=message_id)


@menu.command("click")
def menu_click(
    ctx: typer.Context,
    text: Annotated[str | None, typer.Option("--text")] = None,
    index: Annotated[int | None, typer.Option("--index")] = None,
    match: Annotated[str, typer.Option("--match")] = "contains",
    message_id: MessageId = None,
    env: OptionalEnv = None,
    json_output: Json = False,
):
    # Build the rule inside execute so malformed options get stable JSON errors.
    async def validate_options():
        if (text is None) == (index is None) or match not in {"exact", "contains", "regex"}:
            raise MonitorError("invalid_config", reason="invalid_button_selector")
        if text is not None:
            ButtonMatch(text=text, match=match)

    execute(validate_options)
    rule = ButtonMatch(text=text, match=match) if text is not None else None
    menu_operation(ctx, env, "click", message_id=message_id, rule=rule, index=index)


@menu.command("back")
def menu_back(
    ctx: typer.Context,
    env: OptionalEnv = None,
    message_id: MessageId = None,
    json_output: Json = False,
):
    menu_operation(ctx, env, "back", message_id=message_id)


def main():
    app()
