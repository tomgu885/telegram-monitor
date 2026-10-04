import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import yaml

from telegram_monitor.config import Configuration, Correlation, DeploymentResult
from telegram_monitor.deployer import deploy
from telegram_monitor.exceptions import MonitorError
from telegram_monitor.models import Button
from telegram_monitor.watcher import CorrelationMatcher, match_pattern

from .conftest import message


def rules():
    return DeploymentResult.model_validate(
        {
            "sender_username": "example_bot",
            "success": {"contains": ["完成时间:"]},
            "failure": {"contains": ["FAILURE"]},
            "timeout_seconds": 0.02,
            "correlation": {
                "payment-rpc": {
                    "service_patterns": [r"(?<![\w-])payment-rpc(?![\w-])"],
                    "key_patterns": {"build": r"build=(?P<value>\d+)"},
                }
            },
        }
    )


def result_message(text="payment-rpc build=7 完成时间:", ident=102, **kwargs):
    return replace(message(ident, text=text, buttons=[]), **kwargs)


def matcher():
    return CorrelationMatcher(
        -100123,
        "example_bot",
        100,
        datetime.now(UTC) - timedelta(seconds=1),
        100,
        rules().correlation["payment-rpc"],
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sender_username": "someone_else"},
        {"chat_id": -999},
        {"message_id": 100},
        {"date": datetime.now(UTC) - timedelta(days=1)},
        {"text": "member-rpc 完成时间:"},
        {"text": "payment-rpc-other 完成时间:"},
        {"reply_to_msg_id": 99},
    ],
)
def test_unrelated_results_rejected(kwargs):
    assert not matcher().accepts(replace(result_message(), **kwargs))


def test_build_key_pins_same_service_build_and_reply_chain():
    correlation = matcher()
    assert correlation.accepts(result_message("payment-rpc build=7 queued"))
    assert not correlation.accepts(result_message("payment-rpc build=8 完成时间:", 103))
    assert not correlation.accepts(result_message("payment-rpc 完成时间:", 104))
    assert correlation.accepts(result_message("完成时间:", 105, reply_to_msg_id=102))


def test_edited_trigger_seeds_key_but_cannot_be_terminal_result():
    correlation = matcher()
    ack = result_message("payment-rpc build=7 queued", 100, edit_date=datetime.now(UTC))
    assert correlation.observe_trigger(ack)
    assert not correlation.accepts(ack)
    assert not correlation.accepts(result_message("payment-rpc build=8 完成时间:"))
    assert correlation.accepts(result_message())


def test_failed_notification_can_also_contain_completion_time():
    assert match_pattern("FAILURE 完成时间:", rules().failure) == "FAILURE"


def setup_config(configuration):
    data = yaml.safe_load((configuration.root / "testa.yaml").read_text())
    data["services"] = {
        "payment-rpc": {
            "aliases": ["payment-rpc-testa"],
            "menu_path": [
                {"match": "exact", "text": "RPC 服务"},
                {"match": "exact", "text": "🚀 main发"},
            ],
            "expected_context": ["服务：payment-rpc-testa", "默认分支：main", "命名空间：testa"],
        }
    }
    data["deployment_result"] = rules().model_dump()
    (configuration.root / "testa.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    return Configuration(configuration.path)


class FakeDeployment:
    def __init__(self, texts, action_error=None):
        self.current = message()
        self.subscribed = False
        self.texts = texts
        self.action_error = action_error
        self.trigger_calls = 0
        self.open_menu = AsyncMock(return_value=self.current)
        self.click_button = AsyncMock(side_effect=self.navigate)
        self.latest_message_id = AsyncMock(return_value=100)

    async def get_message(self, _id):
        return self.current

    async def navigate(self, *args):
        self.current = message(
            text="服务：payment-rpc-testa\n默认分支：main\n命名空间：testa",
            buttons=[Button(0, 0, "🚀 main发")],
            revision="next",
        )
        return self.current

    @asynccontextmanager
    async def updates(self):
        self.queue = asyncio.Queue()
        self.subscribed = True
        try:
            yield self.queue, asyncio.Event()
        finally:
            self.subscribed = False

    async def trigger_button(self, *_args):
        assert self.subscribed
        self.trigger_calls += 1
        for i, text in enumerate(self.texts):
            self.queue.put_nowait(result_message(text, 101 + i))
        if self.action_error:
            raise self.action_error


@pytest.mark.parametrize(
    ("texts", "status"),
    [
        (
            [
                "member-rpc 完成时间:",
                "payment-rpc build=7 queued",
                "payment-rpc build=8 完成时间:",
                "payment-rpc build=7 完成时间:",
            ],
            "success",
        ),
        (["payment-rpc FAILURE 完成时间:"], "failed"),
        ([], "timeout"),
    ],
)
def test_deploy_waits_for_own_result_and_records_history(configuration, texts, status):
    config = setup_config(configuration)
    client = FakeDeployment(texts)
    output = asyncio.run(deploy(config, client, "testa", "payment-rpc"))
    assert output["status"] == status
    assert client.trigger_calls == 1
    assert not client.subscribed
    history = [
        json.loads(line)
        for line in (config.state_dir / "deploy-history.jsonl").read_text().splitlines()
    ]
    assert [item["status"] for item in history] == ["triggering", status]


def test_callback_timeout_does_not_repeat_trigger(configuration):
    client = FakeDeployment(["payment-rpc build=7 完成时间:"], action_error=TimeoutError())
    output = asyncio.run(deploy(setup_config(configuration), client, "testa", "payment-rpc-testa"))
    assert output["status"] == "success"
    assert client.trigger_calls == 1


def test_context_mismatch_never_clicks_action(configuration):
    config = setup_config(configuration)
    client = FakeDeployment([])
    original = client.navigate

    async def wrong(*args):
        await original()
        client.current = replace(client.current, text="服务：member-rpc-testa")
        return client.current

    client.click_button.side_effect = wrong
    with pytest.raises(MonitorError, match="unexpected_menu"):
        asyncio.run(deploy(config, client, "testa", "payment-rpc"))
    assert client.trigger_calls == 0


def test_bad_correlation_regex_is_validated():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Correlation(service_patterns=["["])


def test_queued_success_is_not_completion_and_edited_ack_binds_build():
    config = DeploymentResult.model_validate(
        {
            "sender_username": "example_bot",
            "success": {"regex": [r"(?ms)^状态:\s*🟢 SUCCESS\s*$.*^完成时间:"]},
            "failure": {"contains": ["FAILURE"]},
            "correlation": {
                "payment-rpc": {
                    "service_patterns": [
                        r"(?m)^(?:触发项目|服务名称|发布服务名称)[：:]\s*payment-rpc(?:-testa)?\s*$"
                    ],
                    "key_patterns": {"build": r"/job/payment-rpc-testa/(?P<value>\d+)/"},
                }
            },
        }
    )
    correlation = CorrelationMatcher(
        -100123,
        "example_bot",
        100,
        datetime.now(UTC) - timedelta(seconds=1),
        100,
        config.correlation["payment-rpc"],
    )
    queued = "触发项目: payment-rpc-testa\n触发状态: ✅ SUCCESS，已成功加入 Jenkins 构建队列"
    assert correlation.accepts(result_message(queued, 101))
    assert match_pattern(queued, config.success) is None
    ack = result_message(
        queued + "\n构建地址: https://example.test/job/payment-rpc-testa/495/",
        101,
        edit_date=datetime.now(UTC),
    )
    assert correlation.accepts(ack)
    assert correlation.keys == {"build": "495"}
    completed = "状态: 🟢 SUCCESS\n发布服务名称: payment-rpc\n完成时间: now\n"
    assert not correlation.accepts(result_message(completed + "/job/payment-rpc-testa/496/", 102))
    own = result_message(completed + "/job/payment-rpc-testa/495/", 103)
    assert correlation.accepts(own)
    assert match_pattern(own.text, config.success)
