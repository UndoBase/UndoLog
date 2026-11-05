"""Tests for the CrewAI auto-instrumentation helpers.

The SDK has no CrewAI dependency: every test drives the wrapper through
minimal fake crews and tool objects that mimic the duck-typed surface it
relies on (``kickoff_async``, ``agents``, and per-agent ``tools``).

Covers:
    - ``wrap_crewai`` creates a session per invocation
    - The crew runs with the session visible to its tools
    - Agents' tools are instrumented, once, and only after every tool
      on every agent has been accepted
    - ``AwaitingApprovalError`` propagates with the run recorded on the
      facade, so the caller can resolve it and resume
    - Resume keys are consumed by the wrapper and never reach the crew,
      and they choose between replaying a run and continuing its counter
    - ``org_id`` resolution and attribute delegation
"""

from __future__ import annotations

import inspect
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
from undolog_sdk.integrations import WrappedCrew, wrap_crewai
from undolog_sdk.integrations.crewai import classify_tool

# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeAgent:
    """Minimal stand-in for a CrewAI agent: a list of tools."""

    def __init__(self, tools: list[Any]) -> None:
        self.tools = tools


class FakeCrew:
    """Minimal stand-in for a CrewAI crew."""

    def __init__(
        self,
        tools: list[Any] | None = None,
        result: Any = None,
        role: str = "support",
        tasks: list[Any] | None = None,
    ) -> None:
        self.agents: list[Any] = [FakeAgent(tools if tools is not None else [])]
        self.role = role
        self.tasks: list[Any] = tasks if tasks is not None else []
        self.invocations: list[dict[str, Any]] = []
        self._result = "crew output" if result is None else result

    async def kickoff_async(
        self, inputs: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        self.invocations.append({"inputs": inputs, **kwargs})
        return self._result


class FakeStructuredTool:
    """Mimics a LangChain ``StructuredTool``: async callable on ``coroutine``."""

    def __init__(self, name: str, fn: Any) -> None:
        self.name = name
        self.coroutine = fn


class ToolCrew(FakeCrew):
    """A crew whose kickoff runs its agents' tools with fixed arguments."""

    def __init__(self, tool_args: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tool_args = tool_args

    async def kickoff_async(
        self, inputs: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        self.invocations.append({"inputs": inputs, **kwargs})
        results = []
        for tool in self.agents[0].tools:
            target = getattr(tool, "coroutine", None) or tool
            accepted = inspect.signature(target).parameters
            call_args = {k: v for k, v in self.tool_args.items() if k in accepted}
            results.append(await tool(**call_args))
        return results


class MultiAgentToolCrew:
    """A crew executing tools across all agents."""

    def __init__(
        self,
        agents: list[FakeAgent],
        tool_args: dict[str, Any] | None = None,
    ) -> None:
        self.agents = agents
        self.tool_args = tool_args or {}
        self.invocations: list[dict[str, Any]] = []

    async def kickoff_async(
        self, inputs: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        self.invocations.append({"inputs": inputs, **kwargs})
        results = []
        for agent in self.agents:
            for tool in agent.tools:
                target = getattr(tool, "coroutine", None) or tool
                accepted = inspect.signature(target).parameters
                call_args = {k: v for k, v in self.tool_args.items() if k in accepted}
                results.append(await tool(**call_args))
        return results


class FakeTask:
    """Minimal stand-in for a CrewAI task with tools."""

    def __init__(self, tools: list[Any]) -> None:
        self.tools = tools


# ── Session lifecycle ───────────────────────────────────────────────────────


class TestSessionLifecycle:
    """Each ``kickoff_async`` runs in its own session."""

    async def test_session_created_per_invocation(self) -> None:
        crew = wrap_crewai(FakeCrew(), org_id="org_test")

        out = await crew.kickoff_async()

        assert out == "crew output"
        assert crew.session_id, "a session id must be recorded"
        assert crew.step_index == 0
        assert crew.awaiting_approval is False
        assert crew.approval_request is None

    async def test_distinct_invocations_get_distinct_sessions(self) -> None:
        crew = wrap_crewai(FakeCrew(), org_id="org_test")

        await crew.kickoff_async()
        first = crew.session_id
        await crew.kickoff_async()
        second = crew.session_id

        assert first != second, "a second run must not reuse the first session"

    async def test_session_and_org_are_visible_to_the_crew(self) -> None:
        seen: list[tuple[str | None, str | None]] = []

        class SeeingCrew(FakeCrew):
            async def kickoff_async(
                self, inputs: dict[str, Any] | None = None, **kwargs: Any
            ) -> Any:
                session = get_current_session()
                seen.append(
                    (session.session_id, session.org_id) if session else (None, None)
                )
                return "ok"

        crew = wrap_crewai(SeeingCrew(), org_id="org_test")

        await crew.kickoff_async()

        assert seen == [(crew.session_id, "org_test")], (
            "tools resolve the session from the context var, so it must be set "
            "with the organisation that scopes their intercepted calls"
        )

    async def test_step_index_stays_zero_without_work(self) -> None:
        crew = wrap_crewai(FakeCrew(), org_id="org_test")

        await crew.kickoff_async()

        assert crew.step_index == 0

    async def test_crew_output_is_returned_unchanged(self) -> None:
        marker = object()
        crew = wrap_crewai(FakeCrew(result=marker), org_id="org_test")

        out = await crew.kickoff_async()

        assert out is marker, "the facade must not reshape the crew's output"

    async def test_inputs_and_kwargs_are_forwarded(self) -> None:
        inner = FakeCrew()

        await wrap_crewai(inner, org_id="org_test").kickoff_async(
            {"customer": "Alice"}, memory=True
        )

        assert inner.invocations[0]["inputs"] == {"customer": "Alice"}
        assert inner.invocations[0]["memory"] is True

    async def test_empty_inputs_are_passed_as_none(self) -> None:
        """A run with no task placeholders must not hand the crew a dict."""
        inner = FakeCrew()

        await wrap_crewai(inner, org_id="org_test").kickoff_async({})

        assert inner.invocations[0]["inputs"] is None


class TestResumeKeys:
    """``session_id`` and ``undolog_step_index`` belong to the wrapper."""

    async def test_resume_keys_are_consumed_before_forwarding(self) -> None:
        inner = FakeCrew()

        await wrap_crewai(inner, org_id="org_test").kickoff_async(
            {"session_id": "abc", "undolog_step_index": 4, "customer": "Alice"}
        )

        assert inner.invocations[0]["inputs"] == {"customer": "Alice"}, (
            "UndoLog bookkeeping must not surface as a task placeholder"
        )

    async def test_resume_key_restores_the_session(self) -> None:
        first = wrap_crewai(FakeCrew(), org_id="org_test")
        await first.kickoff_async()
        resumed_id = first.session_id

        second = wrap_crewai(FakeCrew(), org_id="org_test")
        await second.kickoff_async({"session_id": resumed_id})

        assert second.session_id == resumed_id

    async def test_resume_key_restores_the_step_index(self) -> None:
        crew = wrap_crewai(FakeCrew(), org_id="org_test")

        await crew.kickoff_async({"undolog_step_index": 7})

        assert crew.step_index == 7

    async def test_retry_without_a_step_key_repeats_the_positions(self) -> None:
        """A retry replays only when it lands on the steps it journaled."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-7"),
            InterceptResponse(outcome="Execute", effect_id="eff-7"),
        )
        crew = _crew_pausing_at_first_call(client)

        with pytest.raises(AwaitingApprovalError):
            await crew.kickoff_async()

        await crew.kickoff_async({"session_id": crew.session_id})

        assert _positions(client) == [1, 1], (
            "the retried call must reuse its step, or the engine sees a new "
            "operation instead of replaying the one already journaled"
        )

    async def test_step_key_continues_the_positions(self) -> None:
        """An explicit step index starts new work in the same journal."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-8"),
            InterceptResponse(outcome="Execute", effect_id="eff-8"),
        )
        crew = _crew_pausing_at_first_call(client)

        with pytest.raises(AwaitingApprovalError):
            await crew.kickoff_async()

        await crew.kickoff_async(
            {"session_id": crew.session_id, "undolog_step_index": crew.step_index}
        )

        assert _positions(client) == [1, 2], (
            "an explicit step index must move the counter past the "
            "journaled steps instead of replaying them"
        )


# ── Tool instrumentation ────────────────────────────────────────────────────


class TestToolInstrumentation:
    """Every agent's tools are instrumented exactly once."""

    async def test_plain_async_tool_is_instrumented(self) -> None:
        async def lookup(ticket_id: str) -> str:
            return ticket_id

        inner = FakeCrew(tools=[lookup])

        await wrap_crewai(
            inner, org_id="org_test", tiers={"lookup": ToolTier.SAFE}
        ).kickoff_async()

        wrapped = inner.agents[0].tools[0]
        assert wrapped is not lookup
        assert wrapped._undolog_tool_name == "lookup"

    async def test_structured_tool_container_is_copied(self) -> None:
        async def lookup(ticket_id: str) -> str:
            return ticket_id

        tool = FakeStructuredTool("lookup", lookup)
        original = tool.coroutine
        inner = FakeCrew(tools=[tool])

        await wrap_crewai(
            inner, org_id="org_test", tiers={"lookup": ToolTier.SAFE}
        ).kickoff_async()

        wrapped = inner.agents[0].tools[0]
        assert wrapped is not tool, "the container must be copied"
        assert tool.coroutine is original, "the caller's tool must be untouched"
        assert wrapped.coroutine is not original

    async def test_tools_are_instrumented_once_across_kickoffs(self) -> None:
        async def lookup(ticket_id: str) -> str:
            return ticket_id

        inner = FakeCrew(tools=[lookup])
        crew = wrap_crewai(inner, org_id="org_test", tiers={"lookup": ToolTier.SAFE})

        await crew.kickoff_async()
        once = inner.agents[0].tools[0]
        await crew.kickoff_async()

        assert inner.agents[0].tools[0] is once, (
            "a second kickoff must not re-wrap and journal calls twice"
        )

    async def test_decorated_tool_needs_no_mapping(self) -> None:
        """The documented flow: pre-decorated tools wrap with no arguments."""

        @undolog_tool(tier=ToolTier.SAFE)
        async def lookup(ticket_id: str) -> str:
            return ticket_id

        inner = FakeCrew(tools=[lookup])

        await wrap_crewai(inner, org_id="org_test").kickoff_async()

        assert inner.agents[0].tools[0] is lookup

    async def test_missing_compensation_raises(self) -> None:
        async def publish(title: str) -> str:
            return title

        crew = wrap_crewai(FakeCrew(tools=[publish]), org_id="org_test")

        with pytest.raises(ValueError, match="compensation name"):
            await crew.kickoff_async()

    async def test_tier_override_skips_compensation_requirement(self) -> None:
        async def publish(title: str) -> str:
            return title

        inner = FakeCrew(tools=[publish])
        crew = wrap_crewai(inner, org_id="org_test", tiers={"publish": ToolTier.SAFE})

        await crew.kickoff_async()

        assert inner.agents[0].tools[0]._undolog_tool_name == "publish"

    async def test_explicit_client_reaches_the_wrapped_tool(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-1"))

        async def escalate(ticket_id: str) -> str:
            return ticket_id.upper()

        inner = ToolCrew({"ticket_id": "tkt-1"}, tools=[escalate])
        crew = wrap_crewai(
            inner,
            org_id="org_test",
            tiers={"escalate": ToolTier.IRREVERSIBLE},
            client=client,
        )

        out = await crew.kickoff_async()

        assert out == ["TKT-1"]
        client.intercept.assert_awaited_once()
        assert client.intercept.call_args.kwargs["tool_name"] == "escalate"

    async def test_sync_tool_is_rejected(self) -> None:
        """A sync tool would fail at call time, after the wrap promised it."""

        def publish(title: str) -> str:
            return title

        crew = wrap_crewai(FakeCrew(tools=[publish]), org_id="org_test")

        with pytest.raises(ValueError, match="cannot instrument"):
            await crew.kickoff_async()

    async def test_structured_tool_with_sync_coroutine_is_rejected(self) -> None:
        def publish(title: str) -> str:
            return title

        crew = wrap_crewai(
            FakeCrew(tools=[FakeStructuredTool("publish", publish)]),
            org_id="org_test",
        )

        with pytest.raises(ValueError, match="must be async"):
            await crew.kickoff_async()

    async def test_uninstrumentable_object_is_rejected(self) -> None:
        crew = wrap_crewai(FakeCrew(tools=[object()]), org_id="org_test")

        with pytest.raises(ValueError, match="cannot instrument"):
            await crew.kickoff_async()

    async def test_failure_leaves_every_agent_untouched(self) -> None:
        """One bad tool must not leave the crew half-instrumented."""

        async def good(x: int) -> int:
            return x

        def bad(x: int) -> int:
            return x

        inner = FakeCrew(tools=[good])
        inner.agents.append(FakeAgent([bad]))
        before = [agent.tools for agent in inner.agents]
        crew = wrap_crewai(inner, org_id="org_test", tiers={"good": ToolTier.SAFE})

        with pytest.raises(ValueError, match="must be async"):
            await crew.kickoff_async()

        assert [agent.tools for agent in inner.agents] == before
        assert inner.agents[0].tools[0] is good

    async def test_agent_without_tools_attribute_raises(self) -> None:
        class BareAgent:
            pass

        inner = FakeCrew()
        inner.agents = [BareAgent()]
        crew = wrap_crewai(inner, org_id="org_test")

        with pytest.raises(AttributeError):
            await crew.kickoff_async()

    async def test_agent_with_no_tools_is_fine(self) -> None:
        crew = wrap_crewai(FakeCrew(tools=[]), org_id="org_test")

        out = await crew.kickoff_async()

        assert out == "crew output"


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

        inner = ToolCrew({"article_id": "art-1"}, tools=[send_newsletter])
        crew = wrap_crewai(inner, org_id="org_test")

        with pytest.raises(AwaitingApprovalError) as caught:
            await crew.kickoff_async()

        assert caught.value.approval_id == "ap-1"
        assert crew.awaiting_approval is True
        assert crew.approval_request == {
            "approval_id": "ap-1",
            "tool_name": "send_newsletter",
            "step_index": 1,
        }
        assert crew.session_id, "the handler must be able to resume this run"

    async def test_run_identity_survives_the_raise(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-2")
        )

        @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
        async def send_newsletter(article_id: str) -> str:
            return article_id

        crew = wrap_crewai(
            ToolCrew({"article_id": "art-2"}, tools=[send_newsletter]),
            org_id="org_test",
        )

        with pytest.raises(AwaitingApprovalError):
            await crew.kickoff_async()

        assert crew.step_index == 1, "the suspended step must be reported"

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

        inner = ToolCrew({"article_id": "Alice"}, tools=[send_newsletter])
        crew = wrap_crewai(inner, org_id="org_test")

        with pytest.raises(AwaitingApprovalError):
            await crew.kickoff_async(inputs={"customer": "Alice"})
        assert crew.awaiting_approval is True
        assert runs == [], "the tool body must not run before approval"

        await crew.kickoff_async(
            inputs={"customer": "Alice", "session_id": crew.session_id}
        )

        assert crew.awaiting_approval is False
        assert crew.approval_request is None
        assert runs == ["Alice"]

    async def test_other_errors_do_not_mark_an_approval(self) -> None:
        class FailingCrew(FakeCrew):
            async def kickoff_async(
                self, inputs: dict[str, Any] | None = None, **kwargs: Any
            ) -> Any:
                raise RuntimeError("llm unavailable")

        crew = wrap_crewai(FailingCrew(), org_id="org_test")

        with pytest.raises(RuntimeError, match="llm unavailable"):
            await crew.kickoff_async()

        assert crew.awaiting_approval is False
        assert crew.approval_request is None

    async def test_a_new_run_clears_a_stale_approval_flag(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-4"),
            InterceptResponse(outcome="Execute", effect_id="eff-4"),
        )

        @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
        async def send_newsletter(article_id: str) -> str:
            return article_id

        inner = ToolCrew({"article_id": "art-4"}, tools=[send_newsletter])
        crew = wrap_crewai(inner, org_id="org_test")
        with pytest.raises(AwaitingApprovalError):
            await crew.kickoff_async()
        assert crew.awaiting_approval is True

        await crew.kickoff_async()

        assert crew.awaiting_approval is False, (
            "a later run that completes must clear the previous flag"
        )
        assert crew.approval_request is None

    async def test_tool_error_is_not_swallowed(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-9"))

        @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
        async def explode(article_id: str) -> str:
            raise RuntimeError("tool blew up")

        crew = wrap_crewai(
            ToolCrew({"article_id": "art-9"}, tools=[explode]),
            org_id="org_test",
        )

        with pytest.raises(RuntimeError, match="tool blew up"):
            await crew.kickoff_async()


# ── Delegation and configuration ────────────────────────────────────────────


class TestDelegation:
    """The facade proxies crew attributes but keeps its own state."""

    async def test_public_attribute_delegates(self) -> None:
        inner = FakeCrew(role="publishing")
        crew = wrap_crewai(inner, org_id="org_test")

        assert crew.role == "publishing"

    async def test_wrapped_crew_exposes_facade_class(self) -> None:
        crew = wrap_crewai(FakeCrew(), org_id="org_test")

        assert isinstance(crew, WrappedCrew)
        assert crew.org_id == "org_test"

    async def test_missing_private_attribute_raises_attribute_error(self) -> None:
        crew = wrap_crewai(FakeCrew(), org_id="org_test")

        with pytest.raises(AttributeError):
            crew._not_a_real_attribute

    async def test_missing_public_attribute_raises_attribute_error(self) -> None:
        crew = wrap_crewai(FakeCrew(), org_id="org_test")

        with pytest.raises(AttributeError):
            crew.kickoff


class TestOrgResolution:
    """``org_id`` resolution: explicit argument wins, then environment."""

    async def test_explicit_org_id_wins_over_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UNDOLOG_ORG_ID", "org_env")

        crew = wrap_crewai(FakeCrew(), org_id="org_explicit")

        assert crew.org_id == "org_explicit"

    async def test_environment_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UNDOLOG_ORG_ID", "org_env")

        crew = wrap_crewai(FakeCrew())

        assert crew.org_id == "org_env"

    async def test_default_when_environment_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UNDOLOG_ORG_ID", raising=False)

        crew = wrap_crewai(FakeCrew())

        assert crew.org_id == "org_demo"


class TestSingleLineIntegration:
    """The whole integration is one wrap call plus one kickoff."""

    async def test_pre_decorated_crew_runs_with_one_wrap(self) -> None:
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

        inner = ToolCrew(
            {"query": "urgent", "title": "report"}, tools=[lookup, publish]
        )
        crew = wrap_crewai(inner, org_id="org_demo")

        out = await crew.kickoff_async()

        assert out == ["found:urgent", "published:report"]
        assert all(
            getattr(tool, "_undolog_tool_name", None) for tool in inner.agents[0].tools
        )
        # The SAFE tool bypasses the proxy, so only publish intercepts.
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


def _crew_pausing_at_first_call(client: AsyncMock) -> WrappedCrew:
    """Build a crew whose single tool waits for approval on its first call.

    Parameters:
        client: UndoLog client stub answering that tool's ``intercept``
            calls.

    Returns:
        A wrapped crew that runs the tool with a fixed argument.
    """

    @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
    async def send_newsletter(article_id: str) -> str:
        return article_id

    return wrap_crewai(
        ToolCrew({"article_id": "art"}, tools=[send_newsletter]),
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


# ── Agent tool discovery ────────────────────────────────────────────────────


class TestAgentToolDiscovery:
    """wrap_crewai discovers and binds tools across all agents and tasks."""

    async def test_discovers_and_binds_tools_across_multiple_agents(self) -> None:
        async def fetch_profile(user_id: str) -> str:
            return f"profile:{user_id}"

        async def check_permissions(user_id: str) -> bool:
            return True

        async def list_groups(user_id: str) -> list[str]:
            return ["admin"]

        agent1 = FakeAgent([fetch_profile, check_permissions])
        agent2 = FakeAgent([list_groups])
        agent3 = FakeAgent([])

        crew = MultiAgentToolCrew(
            [agent1, agent2, agent3],
            tool_args={"user_id": "u-1"},
        )
        wrapped = wrap_crewai(
            crew,
            org_id="org_test",
            tiers={
                "fetch_profile": ToolTier.SAFE,
                "check_permissions": ToolTier.SAFE,
                "list_groups": ToolTier.SAFE,
            },
        )

        results = await wrapped.kickoff_async()
        assert results == ["profile:u-1", True, ["admin"]]
        assert agent1.tools[0]._undolog_tool_name == "fetch_profile"
        assert agent1.tools[1]._undolog_tool_name == "check_permissions"
        assert agent2.tools[0]._undolog_tool_name == "list_groups"
        assert agent3.tools == []

    async def test_discovers_tools_on_tasks_when_present(self) -> None:
        async def read_summary(task_id: str) -> str:
            return f"summary:{task_id}"

        crew = FakeCrew(tools=[])
        task = FakeTask([read_summary])
        crew.tasks = [task]

        wrapped = wrap_crewai(
            crew,
            org_id="org_test",
            tiers={"read_summary": ToolTier.SAFE},
        )
        await wrapped.kickoff_async()

        assert task.tools[0]._undolog_tool_name == "read_summary"


# ── Un-annotated tool categorization and logging ────────────────────────────


class TestUnannotatedToolCategorizationAndLogging:
    """Un-annotated agent tools are categorized and logged with warnings."""

    async def test_unannotated_safe_tool_categorized_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-safe-1"))

        async def fetch_user_data(user_id: str) -> dict[str, str]:
            return {"user": user_id}

        crew = ToolCrew({"user_id": "u-100"}, tools=[fetch_user_data])
        wrapped = wrap_crewai(crew, org_id="org_test", client=client)

        with caplog.at_level("WARNING"):
            out = await wrapped.kickoff_async()

        assert out == [{"user": "u-100"}]
        client.intercept.assert_not_awaited()
        assert any(
            "Un-annotated tool 'fetch_user_data' invoked within crew agent; classified as safe"
            in record.message
            and record.levelname == "WARNING"
            for record in caplog.records
        )

    def test_classify_tool_heuristics(self) -> None:
        async def get_account(id: str) -> None:
            pass

        async def read_configuration() -> None:
            pass

        async def fetch_records() -> None:
            pass

        async def list_items() -> None:
            pass

        async def search_query() -> None:
            pass

        async def delete_account(id: str) -> None:
            pass

        async def drop_schema() -> None:
            pass

        async def charge_customer(amt: int) -> None:
            pass

        async def send_invoice() -> None:
            pass

        async def custom_operation() -> None:
            pass

        assert classify_tool(get_account) == ToolTier.SAFE
        assert classify_tool(read_configuration) == ToolTier.SAFE
        assert classify_tool(fetch_records) == ToolTier.SAFE
        assert classify_tool(list_items) == ToolTier.SAFE
        assert classify_tool(search_query) == ToolTier.SAFE

        assert classify_tool(delete_account) == ToolTier.IRREVERSIBLE
        assert classify_tool(drop_schema) == ToolTier.IRREVERSIBLE
        assert classify_tool(charge_customer) == ToolTier.IRREVERSIBLE
        assert classify_tool(send_invoice) == ToolTier.IRREVERSIBLE

        assert classify_tool(custom_operation) == ToolTier.COMPENSABLE

    def test_classify_tool_preserves_existing_annotation(self) -> None:
        @undolog_tool(tier=ToolTier.IRREVERSIBLE)
        async def fetch_with_side_effect() -> None:
            pass

        assert classify_tool(fetch_with_side_effect) == ToolTier.IRREVERSIBLE


# ── Irreversible tool approval suspension ───────────────────────────────────


class TestIrreversibleToolApprovalSuspension:
    """Un-annotated irreversible tools suspend crew execution awaiting approval."""

    async def test_unannotated_irreversible_tool_suspends_for_approval(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-crew-irrev")
        )

        async def delete_customer_record(record_id: str) -> dict[str, str]:
            return {"deleted": record_id}

        crew = ToolCrew({"record_id": "rec-42"}, tools=[delete_customer_record])
        wrapped = wrap_crewai(crew, org_id="org_test", client=client)

        with caplog.at_level("WARNING"):
            with pytest.raises(AwaitingApprovalError) as exc_info:
                await wrapped.kickoff_async()

        assert exc_info.value.approval_id == "ap-crew-irrev"
        assert exc_info.value.tool_name == "delete_customer_record"
        assert wrapped.awaiting_approval is True
        assert wrapped.approval_request == {
            "approval_id": "ap-crew-irrev",
            "tool_name": "delete_customer_record",
            "step_index": 1,
        }
        assert wrapped.session_id is not None
        assert any(
            "Un-annotated tool 'delete_customer_record' invoked within crew agent; classified as irreversible"
            in record.message
            and record.levelname == "WARNING"
            for record in caplog.records
        )


# ── Stateful replay ─────────────────────────────────────────────────────────


class TestStatefulReplay:
    """Replay restores checkpoint state without duplicating committed effects."""

    async def test_stateful_replay_skips_reexecution_for_committed_effects(
        self,
    ) -> None:
        tool_invocations: list[str] = []
        client = _client(
            InterceptResponse(outcome="Execute", effect_id="eff-rep-1"),
            InterceptResponse(outcome="AwaitingApproval", approval_id="ap-rep-2"),
            InterceptResponse(
                outcome="Replay",
                effect_id="eff-rep-1",
                cached_result={"output": {"status": "recorded", "user_id": "usr-10"}},
            ),
            InterceptResponse(outcome="Execute", effect_id="eff-rep-2"),
        )

        async def record_user_action(user_id: str) -> dict[str, Any]:
            tool_invocations.append(f"record:{user_id}")
            return {"status": "recorded", "user_id": user_id}

        async def delete_user_status(user_id: str) -> dict[str, Any]:
            tool_invocations.append(f"delete:{user_id}")
            return {"status": "deleted", "user_id": user_id}

        crew = ToolCrew(
            {"user_id": "usr-10"},
            tools=[record_user_action, delete_user_status],
        )
        wrapped = wrap_crewai(
            crew,
            org_id="org_test",
            client=client,
            compensations={"record_user_action": "undo_record"},
        )

        with pytest.raises(AwaitingApprovalError):
            await wrapped.kickoff_async()

        assert wrapped.awaiting_approval is True
        assert tool_invocations == ["record:usr-10"]
        session_id = wrapped.session_id
        assert session_id is not None

        second_run = await wrapped.kickoff_async({"session_id": session_id})
        assert client.intercept.call_count == 4
        assert tool_invocations == ["record:usr-10", "delete:usr-10"], (
            "committed tool body must not re-execute on stateful replay"
        )
        assert second_run == [
            {"status": "recorded", "user_id": "usr-10"},
            {"status": "deleted", "user_id": "usr-10"},
        ]
        assert wrapped.awaiting_approval is False
        assert wrapped.approval_request is None
        assert wrapped.session_id == session_id
