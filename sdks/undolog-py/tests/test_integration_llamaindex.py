"""Tests for the LlamaIndex auto-instrumentation helpers.

The SDK has no LlamaIndex dependency: every test drives the wrapper
through minimal fake indexes and tool objects that mimic the
duck-typed surface it relies on (``aquery`` and ``tools``).

Covers:
    - ``wrap_llamaindex`` creates a session per query
    - The index runs with the session visible to its tools
    - Tools are instrumented, once, and only after every tool on the
      index has been accepted
    - ``AwaitingApprovalError`` propagates with the run recorded on the
      facade, so the caller can resolve it and resume
    - Resume keys are consumed by the wrapper and never reach the
      index, and they choose between replaying a run and continuing
      its counter
    - ``org_id`` resolution and attribute delegation
"""

from __future__ import annotations

import inspect
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from undolog_sdk import (
    AwaitingApprovalError,
    CompensationDescriptor,
    ToolTier,
    undolog_tool,
)
from undolog_sdk.client import InterceptResponse
from undolog_sdk.context import get_current_session
from undolog_sdk.integrations import WrappedIndex, wrap_llamaindex

# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeFunctionTool:
    """Mimics a LlamaIndex ``FunctionTool``: async callable on ``coroutine``."""

    def __init__(self, name: str, fn: Any) -> None:
        self.name = name
        self.coroutine = fn


class FakeIndex:
    """Minimal stand-in for a LlamaIndex index: tools and ``aquery``."""

    def __init__(
        self,
        tools: list[Any] | None = None,
        result: Any = None,
        role: str = "support",
    ) -> None:
        self.tools: list[Any] = tools if tools is not None else []
        self.role = role
        self.invocations: list[dict[str, Any]] = []
        self._result = "index output" if result is None else result

    async def aquery(self, query: Any, **kwargs: Any) -> Any:
        self.invocations.append({"query": query, **kwargs})
        return self._result


class ToolIndex(FakeIndex):
    """An index whose ``aquery`` runs its tools with fixed arguments."""

    def __init__(self, tool_args: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tool_args = tool_args

    async def aquery(self, query: Any, **kwargs: Any) -> Any:
        self.invocations.append({"query": query, **kwargs})
        results = []
        for tool in self.tools:
            target = getattr(tool, "coroutine", None) or tool
            accepted = inspect.signature(target).parameters
            call_args = {k: v for k, v in self.tool_args.items() if k in accepted}
            results.append(await target(**call_args))
        return results


# ── Session lifecycle ───────────────────────────────────────────────────────


class TestSessionLifecycle:
    """Each ``aquery`` runs in its own session."""

    async def test_the_facade_reports_no_run_before_the_first_query(self) -> None:
        """The documented pre-run state is what a caller reads first."""
        index = wrap_llamaindex(FakeIndex(), org_id="org_test")

        assert index.session_id is None
        assert index.step_index == 0
        assert index.awaiting_approval is False
        assert index.approval_request is None

    async def test_session_created_per_invocation(self) -> None:
        index = wrap_llamaindex(FakeIndex(), org_id="org_test")

        out = await index.aquery("find the report")

        assert out == "index output"
        assert index.session_id, "a session id must be recorded"
        assert index.step_index == 0
        assert index.awaiting_approval is False
        assert index.approval_request is None

    async def test_distinct_invocations_get_distinct_sessions(self) -> None:
        index = wrap_llamaindex(FakeIndex(), org_id="org_test")

        await index.aquery("first")
        first = index.session_id
        await index.aquery("second")
        second = index.session_id

        assert first != second, "a second query must not reuse the first session"

    async def test_session_and_org_are_visible_to_the_index(self) -> None:
        seen: list[tuple[str | None, str | None]] = []

        class SeeingIndex(FakeIndex):
            async def aquery(self, query: Any, **kwargs: Any) -> Any:
                session = get_current_session()
                seen.append(
                    (session.session_id, session.org_id) if session else (None, None)
                )
                return "ok"

        index = wrap_llamaindex(SeeingIndex(), org_id="org_test")

        await index.aquery("anything")

        assert seen == [(index.session_id, "org_test")], (
            "tools resolve the session from the context var, so it must be set "
            "with the organisation that scopes their intercepted calls"
        )

    async def test_the_tools_see_the_session(self) -> None:
        """The context var is what carries the session to a tool call."""
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-1"))

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_search"),
            client=client,
        )
        async def search(query: str) -> str:
            return f"found:{query}"

        index = wrap_llamaindex(
            ToolIndex({"query": "report"}, tools=[search]),
            org_id="org_test",
        )

        await index.aquery("find the report")

        assert client.intercept.call_args.kwargs["session_id"] == index.session_id

    async def test_step_index_stays_zero_without_work(self) -> None:
        index = wrap_llamaindex(FakeIndex(), org_id="org_test")

        await index.aquery("nothing to journal")

        assert index.step_index == 0

    async def test_output_is_returned_unchanged(self) -> None:
        marker = object()
        index = wrap_llamaindex(FakeIndex(result=marker), org_id="org_test")

        out = await index.aquery("anything")

        assert out is marker, "the facade must not reshape the index's output"

    async def test_query_and_kwargs_are_forwarded(self) -> None:
        inner = FakeIndex()
        index = wrap_llamaindex(inner, org_id="org_test")

        await index.aquery("summarize", streaming=True)

        assert inner.invocations == [{"query": "summarize", "streaming": True}], (
            "only the UndoLog keywords may be removed"
        )

    async def test_a_non_string_query_is_forwarded(self) -> None:
        class QueryBundle:
            """Stands in for LlamaIndex's QueryBundle."""

        bundle = QueryBundle()
        inner = FakeIndex()
        index = wrap_llamaindex(inner, org_id="org_test")

        out = await index.aquery(bundle)

        assert out == "index output"
        assert inner.invocations[0]["query"] is bundle

    async def test_an_index_with_no_tools_still_queries(self) -> None:
        index = wrap_llamaindex(FakeIndex(tools=[]), org_id="org_test")

        out = await index.aquery("no tools yet")

        assert out == "index output"
        assert index.session_id


# ── Tool instrumentation ────────────────────────────────────────────────────


class TestToolInstrumentation:
    """Tools are prepared on the way in, atomically and idempotently."""

    async def test_plain_async_tool_is_instrumented(self) -> None:
        good = _async_tool("publish")
        inner = FakeIndex(tools=[good])
        index = wrap_llamaindex(
            inner, org_id="org_test", tiers={"publish": ToolTier.SAFE}
        )

        await index.aquery("anything")

        assert getattr(inner.tools[0], "_undolog_tool_name", None) == "publish"
        assert inner.tools[0] is not good, "the facade must assign a wrapped copy"

    async def test_function_tool_container_is_copied(self) -> None:
        async def publish(title: str) -> str:
            return title

        container = FakeFunctionTool("publish", publish)
        inner = FakeIndex(tools=[container])
        index = wrap_llamaindex(
            inner, org_id="org_test", tiers={"publish": ToolTier.SAFE}
        )

        await index.aquery("anything")

        assigned = inner.tools[0]
        assert assigned is not container, "the caller's tool object must survive"
        assert container.coroutine is publish, (
            "the original container must keep the raw callable, or every later "
            "wrap would see an already instrumented tool"
        )
        assert getattr(assigned.coroutine, "_undolog_tool_name", None) == "publish"

    async def test_tools_are_instrumented_once_across_queries(self) -> None:
        good = _async_tool("publish")
        inner = FakeIndex(tools=[good])
        index = wrap_llamaindex(
            inner, org_id="org_test", tiers={"publish": ToolTier.SAFE}
        )

        await index.aquery("first")
        first = inner.tools[0]
        await index.aquery("second")

        assert inner.tools[0] is first, (
            "a second query must not re-wrap: the same instrumented tool is "
            "kept, so a call is journaled once"
        )

    async def test_a_rejected_tool_leaves_the_index_untouched(self) -> None:
        """Every tool is accepted before the list is reassigned."""

        async def good(x: int) -> int:
            return x

        def bad(x: int) -> int:
            return x

        tools = [good, bad]
        inner = FakeIndex(tools=tools)
        index = wrap_llamaindex(inner, org_id="org_test", tiers={"good": ToolTier.SAFE})

        with pytest.raises(ValueError, match="must be async"):
            await index.aquery("anything")

        assert inner.tools is tools, "no reassignment may happen on failure"
        assert inner.tools[0] is good

    async def test_index_without_tools_attribute_raises(self) -> None:
        class BareIndex:
            async def aquery(self, query: Any, **kwargs: Any) -> Any:
                return "ok"

        index = wrap_llamaindex(BareIndex(), org_id="org_test")

        with pytest.raises(AttributeError, match="expose 'tools'"):
            await index.aquery("anything")

        assert index.session_id is None, (
            "the refusal must come before the session opens, or a query that "
            "cannot be instrumented would still report a run"
        )

    async def test_sync_tool_is_rejected(self) -> None:
        """A sync tool would fail at call time, after the wrap promised it."""

        def publish(title: str) -> str:
            return title

        index = wrap_llamaindex(FakeIndex(tools=[publish]), org_id="org_test")

        with pytest.raises(ValueError, match="cannot instrument"):
            await index.aquery("anything")

    async def test_function_tool_with_sync_coroutine_is_rejected(self) -> None:
        def publish(title: str) -> str:
            return title

        index = wrap_llamaindex(
            FakeIndex(tools=[FakeFunctionTool("publish", publish)]),
            org_id="org_test",
        )

        with pytest.raises(ValueError, match="must be async"):
            await index.aquery("anything")

    async def test_nameless_tool_is_rejected(self) -> None:
        async def publish(title: str) -> str:
            return title

        class NamelessContainer:
            """A FunctionTool-shaped object carrying no name."""

            def __init__(self, fn: Any) -> None:
                self.coroutine = fn

        inner = FakeIndex(tools=[NamelessContainer(publish)])
        index = wrap_llamaindex(inner, org_id="org_test")

        with pytest.raises(ValueError, match="with a name"):
            await index.aquery("anything")

    async def test_uninstrumentable_object_is_rejected(self) -> None:
        index = wrap_llamaindex(FakeIndex(tools=[object()]), org_id="org_test")

        with pytest.raises(ValueError, match="cannot instrument"):
            await index.aquery("anything")

    async def test_missing_compensation_raises(self) -> None:
        index = wrap_llamaindex(
            FakeIndex(tools=[_async_tool("publish")]), org_id="org_test"
        )

        with pytest.raises(ValueError, match="has no compensation name"):
            await index.aquery("anything")

    async def test_tier_override_skips_compensation_requirement(self) -> None:
        index = wrap_llamaindex(
            FakeIndex(tools=[_async_tool("publish")]),
            org_id="org_test",
            tiers={"publish": ToolTier.IRREVERSIBLE},
        )

        await index.aquery("anything")

        assert index.step_index == 0

    async def test_decorated_tool_needs_no_mapping(self) -> None:
        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo"),
        )
        async def publish(title: str) -> str:
            return title

        inner = FakeIndex(tools=[publish])
        index = wrap_llamaindex(inner, org_id="org_test")

        await index.aquery("anything")

        assert inner.tools[0] is publish

    async def test_explicit_client_reaches_the_wrapped_tool(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-2"))
        index = wrap_llamaindex(
            ToolIndex({"query": "urgent"}, tools=[_async_tool("search")]),
            org_id="org_test",
            tiers={"search": ToolTier.IRREVERSIBLE},
            client=client,
        )

        await index.aquery("urgent")

        client.intercept.assert_awaited_once()
        assert client.intercept.call_args.kwargs["tool_name"] == "search"

    async def test_tier_decides_whether_the_proxy_is_reached(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-3"))
        index = wrap_llamaindex(
            ToolIndex({"query": "urgent"}, tools=[_async_tool("search")]),
            org_id="org_test",
            tiers={"search": ToolTier.SAFE},
            client=client,
        )

        await index.aquery("urgent")

        # The SAFE tier bypasses the proxy, so nothing is intercepted.
        client.intercept.assert_not_awaited()


# ── Resume keys ─────────────────────────────────────────────────────────────


class TestResumeKeys:
    """Resume keys are consumed by the facade, never forwarded to the index."""

    async def test_resume_keys_are_consumed_before_forwarding(self) -> None:
        inner = FakeIndex()
        index = wrap_llamaindex(inner, org_id="org_test")

        await index.aquery(
            "retry",
            session_id="sess-123",
            undolog_step_index=4,
        )

        assert inner.invocations == [{"query": "retry"}], (
            "UndoLog bookkeeping must not reach the index as a query option"
        )
        assert index.session_id == "sess-123"
        assert index.step_index == 4

    async def test_other_kwargs_are_forwarded(self) -> None:
        inner = FakeIndex()
        index = wrap_llamaindex(inner, org_id="org_test")

        await index.aquery("retry", streaming=True)

        assert inner.invocations == [{"query": "retry", "streaming": True}]

    async def test_a_string_step_index_is_accepted(self) -> None:
        index = wrap_llamaindex(FakeIndex(), org_id="org_test")

        await index.aquery("retry", session_id="sess-9", undolog_step_index="7")

        assert index.session_id == "sess-9"
        assert index.step_index == 7

    async def test_retry_without_a_step_key_repeats_the_positions(self) -> None:
        """A retry replays only when it lands on the steps it journaled."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-7"),
            InterceptResponse(outcome="Execute", effect_id="eff-7"),
        )
        index = _index_pausing_at_first_call(client)

        with pytest.raises(AwaitingApprovalError):
            await index.aquery("send")

        await index.aquery("send", session_id=index.session_id)

        assert _positions(client) == [1, 1], (
            "the retried call must reuse its step, or the engine sees a new "
            "operation instead of replaying the one already journaled"
        )

    async def test_retry_reaches_the_engine_under_the_same_session(self) -> None:
        """``call_signature`` hashes the session id, so replay needs it."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-8"),
            InterceptResponse(outcome="Execute", effect_id="eff-8"),
        )
        index = _index_pausing_at_first_call(client)

        with pytest.raises(AwaitingApprovalError):
            await index.aquery("send")
        session_id = index.session_id

        await index.aquery("send", session_id=session_id)

        seen = [call.kwargs["session_id"] for call in client.intercept.call_args_list]
        assert seen[0] == seen[1], (
            "the retried call must reach the engine under the same session id, "
            "or call_signature differs and the engine runs it again"
        )

    async def test_step_key_continues_the_positions(self) -> None:
        """An explicit step index starts new work in the same journal."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-9"),
            InterceptResponse(outcome="Execute", effect_id="eff-9"),
        )
        index = _index_pausing_at_first_call(client)

        with pytest.raises(AwaitingApprovalError):
            await index.aquery("send")

        await index.aquery(
            "send",
            session_id=index.session_id,
            undolog_step_index=index.step_index,
        )

        assert _positions(client) == [1, 2], (
            "an explicit step index must move the counter past the "
            "journaled steps instead of replaying them"
        )


# ── Approvals ───────────────────────────────────────────────────────────────


class TestApprovalPropagation:
    """``AwaitingApprovalError`` escapes with the run recorded on the facade."""

    async def test_error_propagates_and_run_is_recorded(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-1")
        )

        @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
        async def send_newsletter(article_id: str) -> str:
            raise AssertionError("body must not run before approval")

        inner = ToolIndex({"article_id": "art-1"}, tools=[send_newsletter])
        index = wrap_llamaindex(inner, org_id="org_test")

        with pytest.raises(AwaitingApprovalError) as caught:
            await index.aquery("send")

        assert caught.value.approval_id == "ap-1"
        assert index.awaiting_approval is True
        assert index.approval_request == {
            "approval_id": "ap-1",
            "tool_name": "send_newsletter",
            "step_index": 1,
        }
        assert index.session_id, "the handler must be able to resume this run"

    async def test_run_identity_survives_the_raise(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-2")
        )

        @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
        async def send_newsletter(article_id: str) -> str:
            return article_id

        index = wrap_llamaindex(
            ToolIndex({"article_id": "art-2"}, tools=[send_newsletter]),
            org_id="org_test",
        )

        with pytest.raises(AwaitingApprovalError):
            await index.aquery("send")

        assert index.step_index == 1, "the suspended step must be reported"

    async def test_resume_after_approval_clears_the_state(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-3"),
            InterceptResponse(outcome="Execute", effect_id="eff-3"),
        )
        runs: list[str] = []

        @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
        async def send_newsletter(article_id: str) -> str:
            runs.append(article_id)
            return "sent"

        index = wrap_llamaindex(
            ToolIndex({"article_id": "Alice"}, tools=[send_newsletter]),
            org_id="org_test",
        )

        with pytest.raises(AwaitingApprovalError):
            await index.aquery("send")
        assert index.awaiting_approval is True
        assert runs == [], "the tool body must not run before approval"

        await index.aquery("send", session_id=index.session_id)

        assert index.awaiting_approval is False
        assert index.approval_request is None
        assert runs == ["Alice"]

    async def test_other_errors_do_not_mark_an_approval(self) -> None:
        class FailingIndex(FakeIndex):
            async def aquery(self, query: Any, **kwargs: Any) -> Any:
                raise RuntimeError("index exploded")

        index = wrap_llamaindex(FailingIndex(), org_id="org_test")

        with pytest.raises(RuntimeError, match="index exploded"):
            await index.aquery("anything")

        assert index.awaiting_approval is False
        assert index.approval_request is None

    async def test_a_new_run_clears_a_stale_approval_flag(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-4"),
            InterceptResponse(outcome="Execute", effect_id="eff-4"),
        )
        index = wrap_llamaindex(
            ToolIndex({"article_id": "art"}, tools=[_pausing_tool(client)]),
            org_id="org_test",
        )

        with pytest.raises(AwaitingApprovalError):
            await index.aquery("send")
        await index.aquery("send", session_id=index.session_id)

        assert index.awaiting_approval is False
        assert index.approval_request is None

    async def test_a_tool_error_is_not_swallowed(self) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def broken(x: int) -> int:
            raise ValueError("tool body failed")

        index = wrap_llamaindex(ToolIndex({"x": 1}, tools=[broken]), org_id="org_test")

        with pytest.raises(ValueError, match="tool body failed"):
            await index.aquery("anything")

    async def test_the_approval_is_logged_with_its_run(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The log line carries what an operator needs to resolve it."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-5")
        )
        index = wrap_llamaindex(
            ToolIndex({"article_id": "art"}, tools=[_pausing_tool(client)]),
            org_id="org_test",
        )

        with caplog.at_level(
            logging.INFO, logger="undolog_sdk.integrations.llamaindex"
        ):
            with pytest.raises(AwaitingApprovalError):
                await index.aquery("send")

        line = next(
            record.message
            for record in caplog.records
            if "approval required" in record.message
        )
        session_id = index.session_id
        assert session_id is not None, "the log must identify the run"
        assert "send_newsletter" in line
        assert "ap-5" in line
        assert session_id in line


# ── Delegation ──────────────────────────────────────────────────────────────


class TestDelegation:
    """Attributes the facade does not define resolve on the index."""

    async def test_public_attribute_delegates(self) -> None:
        index = wrap_llamaindex(FakeIndex(role="analyst"), org_id="org_test")

        assert index.role == "analyst"

    async def test_wrapped_index_exposes_facade_class(self) -> None:
        index = wrap_llamaindex(FakeIndex(), org_id="org_test")

        assert isinstance(index, WrappedIndex)

    async def test_missing_private_attribute_raises_attribute_error(self) -> None:
        index = wrap_llamaindex(FakeIndex(), org_id="org_test")

        with pytest.raises(AttributeError):
            index._absent

    async def test_missing_public_attribute_raises_attribute_error(self) -> None:
        index = wrap_llamaindex(FakeIndex(), org_id="org_test")

        with pytest.raises(AttributeError):
            index.absent


# ── Organisation resolution ─────────────────────────────────────────────────


class TestOrgResolution:
    """``org_id`` resolves explicitly, then from the environment, then default."""

    async def test_explicit_org_id_wins_over_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UNDOLOG_ORG_ID", "org_env")

        index = wrap_llamaindex(FakeIndex(), org_id="org_explicit")

        assert index.org_id == "org_explicit"

    async def test_environment_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UNDOLOG_ORG_ID", "org_env")

        index = wrap_llamaindex(FakeIndex())

        assert index.org_id == "org_env"

    async def test_default_when_environment_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UNDOLOG_ORG_ID", raising=False)

        index = wrap_llamaindex(FakeIndex())

        assert index.org_id == "org_demo"

    async def test_the_resolved_org_reaches_the_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UNDOLOG_ORG_ID", "org_env")
        seen: list[str | None] = []

        class SeeingIndex(FakeIndex):
            async def aquery(self, query: Any, **kwargs: Any) -> Any:
                session = get_current_session()
                seen.append(session.org_id if session else None)
                return "ok"

        index = wrap_llamaindex(SeeingIndex())

        await index.aquery("anything")

        assert seen == ["org_env"]


# ── Single-line integration ─────────────────────────────────────────────────


class TestSingleLineIntegration:
    """The whole integration is one wrap call plus one query."""

    async def test_pre_decorated_index_runs_with_one_wrap(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-2"))

        @undolog_tool(tier=ToolTier.SAFE, client=client)
        async def lookup(query: str) -> str:
            return f"found:{query}"

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_publish"),
            client=client,
        )
        async def publish(title: str) -> str:
            return f"published:{title}"

        inner = ToolIndex(
            {"query": "urgent", "title": "report"}, tools=[lookup, publish]
        )
        index = wrap_llamaindex(inner, org_id="org_demo")

        out = await index.aquery("summarize")

        assert out == ["found:urgent", "published:report"]
        assert all(getattr(tool, "_undolog_tool_name", None) for tool in inner.tools)
        # The SAFE tool bypasses the proxy, so only publish intercepts.
        client.intercept.assert_awaited_once()

    async def test_raw_tools_classified_in_one_wrap_call(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-5"))
        inner = ToolIndex({"query": "urgent"}, tools=[_async_tool("search")])

        index = wrap_llamaindex(
            inner,
            org_id="org_demo",
            tiers={"search": ToolTier.IRREVERSIBLE},
            client=client,
        )

        out = await index.aquery("summarize")

        assert out == [None]
        assert index.session_id
        client.intercept.assert_awaited_once()


# ── Helpers ─────────────────────────────────────────────────────────────────


def _client(*outcomes: InterceptResponse) -> AsyncMock:
    """Build an UndoLog client stub returning ``outcomes`` in call order.

    Parameters:
        outcomes: One ``InterceptResponse`` per expected ``intercept``
            call, consumed sequentially. An unexpected extra call fails
            the test.

    Returns:
        An ``AsyncMock`` accepted anywhere ``undolog_tool`` takes a
            client, following the stub convention in ``test_decorators``.
    """
    client = AsyncMock()
    client.intercept.side_effect = list(outcomes)
    return client


def _async_tool(name: str) -> Any:
    """Build a plain async tool whose body is irrelevant to the test.

    Parameters:
        name: Tool name, which is also the tier key a test may pass.

    Returns:
        An async function returning ``None``, ready to be indexed.
    """

    async def tool(query: str = "anything") -> None:
        return None

    tool.__name__ = name
    return tool


def _pausing_tool(client: AsyncMock) -> Any:
    """Build an irreversible tool that waits for approval on first use.

    Parameters:
        client: UndoLog client stub answering the tool's ``intercept``
            calls.

    Returns:
        An instrumented tool that returns its argument.
    """

    @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
    async def send_newsletter(article_id: str) -> str:
        return article_id

    return send_newsletter


def _index_pausing_at_first_call(client: AsyncMock) -> WrappedIndex:
    """Build an index whose single tool waits for approval on its first call.

    Parameters:
        client: UndoLog client stub answering that tool's ``intercept``
            calls.

    Returns:
        A wrapped index that runs the tool with a fixed argument.
    """
    return wrap_llamaindex(
        ToolIndex({"article_id": "art"}, tools=[_pausing_tool(client)]),
        org_id="org_test",
    )


def _positions(client: AsyncMock) -> list[int]:
    """Step index of every ``intercept`` call, in call order.

    Parameters:
        client: UndoLog client stub whose calls are recorded.

    Returns:
        The ``step_index`` each call was made with.
    """
    return [call.kwargs["step_index"] for call in client.intercept.call_args_list]
