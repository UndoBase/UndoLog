"""Tests for the Semantic Kernel auto-instrumentation helpers.

The SDK has no Semantic Kernel dependency: every test drives the wrapper
through a minimal fake kernel that mimics the duck-typed surface it
relies on (``add_function``, ``invoke``, and ``invoke_prompt``).

Covers:
    - ``wrap_semantic_kernel`` opens a session per invocation
    - An invoked function runs with the session visible to it
    - Functions are instrumented when they are registered, once, and
      only after every function of a plugin has been accepted
    - ``AwaitingApprovalError`` propagates with the run recorded on the
      facade, so the caller can resolve it and resume
    - Resume keys are consumed by the wrapper and never reach the
      kernel, and they choose between replaying a run and continuing
      its counter
    - ``org_id`` resolution and attribute delegation
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock

import pytest

from undolog_sdk import (
    AwaitingApprovalError,
    ToolTier,
    undolog_tool,
)
from undolog_sdk.client import InterceptResponse
from undolog_sdk.context import get_current_session
from undolog_sdk.integrations import WrappedKernel, wrap_semantic_kernel

# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeKernel:
    """Minimal stand-in for a Semantic Kernel kernel.

    Registered functions are stored by plugin and name, and an
    invocation calls the function it is handed, recording what the
    kernel saw: the arguments, and the session that was in scope.
    """

    def __init__(self, result: Any = None) -> None:
        self.plugins: dict[str, dict[str, Any]] = {}
        self.invocations: list[dict[str, Any]] = []
        self._result = "kernel output" if result is None else result

    def add_function(
        self,
        plugin_name: str,
        function_name: str,
        func: Any,
        **kwargs: Any,
    ) -> str:
        """Register ``func`` under a plugin name, as the kernel does."""
        self.plugins.setdefault(plugin_name, {})[function_name] = func
        return f"{plugin_name}.{function_name}"

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        """Call the function the caller names, with its arguments."""
        self.invocations.append(
            {
                "args": args,
                "kwargs": dict(kwargs),
                "session": get_current_session(),
            }
        )
        target = kwargs.get("function")
        if target is None and args:
            target = args[0]
        arguments = kwargs.get("arguments", {})
        if callable(target):
            # Semantic Kernel binds an argument mapping to the function's
            # own signature; mimic that so a positional ``article_id``
            # is required rather than silently defaulted.
            signature = inspect.signature(target)
            for name in list(arguments):
                if name not in signature.parameters:
                    raise TypeError(f"unexpected argument {name!r}")
            return await target(**arguments)
        return self._result

    async def invoke_prompt(self, *args: Any, **kwargs: Any) -> Any:
        """Run the functions the prompt names, then report the session.

        A Semantic Kernel prompt reaches the proxy only through the
        plugin functions it calls, so the fake calls the same function
        the plugin registered under ``function_name``.
        """
        self.invocations.append(
            {
                "args": args,
                "kwargs": dict(kwargs),
                "session": get_current_session(),
            }
        )
        function = self.plugins.get(kwargs.get("plugin_name", ""), {}).get(
            kwargs.get("function_name", "")
        )
        if function is not None:
            return await function(**kwargs.get("arguments", {}))
        return self._result


async def _lookup_customer(customer_id: str) -> dict[str, str]:
    """Look a customer up (async, so UndoLog can intercept it)."""
    return {"customer_id": customer_id}


async def _search_documents(query: str) -> dict[str, str]:
    """Search the docs (read-only, so it classifies as SAFE)."""
    return {"query": query}


def _sync_search(query: str) -> str:
    """Search the docs synchronously, which UndoLog cannot intercept."""
    return query


class _Container:
    """Mimics a tool object exposing its callable as ``coroutine``.

    CrewAI's StructuredTool has that shape, and the guard the wrappers
    share accepts it, so Semantic Kernel must keep accepting it too.
    """

    def __init__(self, name: str, fn: Any) -> None:
        self.name = name
        self.coroutine = fn


def _nameless_tool() -> Any:
    """Build an async callable that carries no ``__name__``."""
    return _NamelessTool()


class _NamelessTool:
    """Async callable with no ``__name__``, unlike a plain function.

    A kernel registers a function under a name, and the journal records
    the tool by it, so this shape has to be refused rather than
    registered as something unrecognisable.
    """

    async def __call__(self) -> str:
        return "x"


# ── Session lifecycle ───────────────────────────────────────────────────────


class TestSessionLifecycle:
    """Each invocation opens a session its functions can see."""

    async def test_an_invoked_function_sees_the_session(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.SAFE},
        )
        wrapped.add_function("support", "lookup_customer", _lookup_customer)

        await wrapped.invoke(
            kernel.plugins["support"]["lookup_customer"],
            arguments={"customer_id": "cust_42"},
        )

        assert kernel.invocations[0]["session"] is not None, (
            "a registered function must resolve the session from the context var"
        )

    async def test_invoke_prompt_opens_a_session(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        await wrapped.invoke_prompt(
            function_name="chat", plugin_name="support", prompt="hello"
        )

        assert kernel.invocations[0]["session"] is not None, (
            "invoke_prompt is an entry point, so it opens a session too"
        )

    async def test_each_invocation_gets_a_fresh_session(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        first = await wrapped.invoke(function="lookup_customer")
        second = await wrapped.invoke(function="lookup_customer")

        assert first == second == "kernel output"
        assert wrapped.session_id is not None
        seen = [call["session"].session_id for call in kernel.invocations]
        assert seen[0] != seen[1], "a second invocation must not reuse the journal"

    async def test_the_facade_reports_the_session_the_kernel_saw(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        await wrapped.invoke(function="lookup_customer")

        assert wrapped.session_id == kernel.invocations[0]["session"].session_id
        assert wrapped.step_index == 0
        assert wrapped.awaiting_approval is False
        assert wrapped.approval_request is None


# ── session() for entry points the facade does not wrap ─────────────────────


class TestSessionContextManager:
    """``session()`` covers an agent run the facade cannot wrap itself."""

    async def test_a_call_inside_the_block_sees_the_session(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-1"))
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.IRREVERSIBLE},
            client=client,
        )
        wrapped.add_function("support", "lookup_customer", _lookup_customer)
        function = kernel.plugins["support"]["lookup_customer"]

        # An agent reaches a plugin function without going through
        # invoke, so the block is what publishes the session.
        async with wrapped.session():
            result = await function(customer_id="cust_42")

        assert result == {"customer_id": "cust_42"}
        assert _positions(client) == [1]

    async def test_the_facade_reports_the_session_while_open(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        async with wrapped.session() as session:
            assert wrapped.session_id == session.session_id
            assert wrapped.awaiting_approval is False

    async def test_progress_is_reported_once_the_block_closes(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-2"))
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.IRREVERSIBLE},
            client=client,
        )
        wrapped.add_function("support", "lookup_customer", _lookup_customer)
        function = kernel.plugins["support"]["lookup_customer"]

        async with wrapped.session():
            await function(customer_id="cust_42")

        assert wrapped.step_index == 1

    async def test_each_block_gets_a_fresh_session(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        async with wrapped.session() as first:
            first_id = first.session_id
        async with wrapped.session() as second:
            second_id = second.session_id

        assert first_id != second_id, "a second block must not reuse the journal"

    async def test_resuming_a_block_reuses_the_journal(self) -> None:
        """The retry recipe for an agent run: the same session id, so the
        calls that completed replay instead of running again."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-r"),
            InterceptResponse(outcome="Execute", effect_id="eff-r"),
        )
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.IRREVERSIBLE},
            client=client,
        )
        wrapped.add_function("support", "lookup_customer", _lookup_customer)
        function = kernel.plugins["support"]["lookup_customer"]

        with pytest.raises(AwaitingApprovalError):
            async with wrapped.session():
                await function(customer_id="cust_42")
        session_id = wrapped.session_id

        async with wrapped.session(session_id=session_id):
            await function(customer_id="cust_42")

        seen = [call.kwargs["session_id"] for call in client.intercept.call_args_list]
        assert seen[0] == seen[1], (
            "the retried call must reach the engine under the same session id, "
            "or call_signature differs and the engine runs it again"
        )
        assert _positions(client) == [1, 1], (
            "the retried call must reuse its step, or the engine sees a new "
            "operation instead of replaying the one already journaled"
        )

    async def test_start_step_continues_the_positions(self) -> None:
        client = _client(
            InterceptResponse(outcome="Execute", effect_id="eff-c"),
            InterceptResponse(outcome="Execute", effect_id="eff-d"),
        )
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.IRREVERSIBLE},
            client=client,
        )
        wrapped.add_function("support", "lookup_customer", _lookup_customer)
        function = kernel.plugins["support"]["lookup_customer"]

        async with wrapped.session():
            await function(customer_id="cust_42")
        async with wrapped.session(
            session_id=wrapped.session_id, start_step=wrapped.step_index
        ):
            await function(customer_id="cust_42")

        assert _positions(client) == [1, 2], (
            "an explicit start step must move the counter past the "
            "journaled steps instead of replaying them"
        )

    async def test_an_approval_inside_the_block_is_recorded(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-s")
        )
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.IRREVERSIBLE},
            client=client,
        )
        wrapped.add_function("support", "lookup_customer", _lookup_customer)
        function = kernel.plugins["support"]["lookup_customer"]

        with pytest.raises(AwaitingApprovalError):
            async with wrapped.session():
                await function(customer_id="cust_42")

        assert wrapped.awaiting_approval is True
        assert wrapped.session_id is not None
        assert wrapped.approval_request == {
            "approval_id": "approval-s",
            "tool_name": "_lookup_customer",
            "step_index": 1,
        }


# ── Resume keys ─────────────────────────────────────────────────────────────


class TestResumeKeys:
    """The wrapper consumes the resume keys before the kernel reads them."""

    async def test_resume_keys_never_reach_the_kernel(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")
        arguments = {
            "customer_id": "cust_42",
            "session_id": "session-1",
            "undolog_step_index": 3,
        }

        await wrapped.invoke(function="lookup_customer", arguments=arguments)

        forwarded = kernel.invocations[0]["kwargs"]["arguments"]
        assert "session_id" not in forwarded
        assert "undolog_step_index" not in forwarded
        assert forwarded == {"customer_id": "cust_42"}

    async def test_positional_arguments_are_stripped(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")
        arguments = {"customer_id": "cust_42", "session_id": "session-1"}

        await wrapped.invoke("lookup_customer", arguments)

        forwarded = kernel.invocations[0]["args"][1]
        assert forwarded == {"customer_id": "cust_42"}

    async def test_a_call_without_arguments_opens_a_new_session(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        await wrapped.invoke(function="lookup_customer")

        assert wrapped.session_id is not None
        assert wrapped.step_index == 0

    async def test_an_immutable_mapping_carrying_a_resume_key_is_refused(
        self,
    ) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")
        arguments: dict[str, Any] = {"session_id": "session-1"}
        sealed = _sealed(arguments)

        with pytest.raises(TypeError, match="mutable"):
            await wrapped.invoke(function="lookup_customer", arguments=sealed)

        assert kernel.invocations == [], "nothing may run behind bookkeeping"

    async def test_omitted_step_key_restarts_the_positions(self) -> None:
        """The retry recipe: ``session_id`` alone replays the run."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-1"),
            InterceptResponse(outcome="Execute", effect_id="eff-1"),
        )
        kernel = _kernel_pausing_at_first_call(client)
        function = kernel.plugins["support"]["send_newsletter"]

        with pytest.raises(AwaitingApprovalError):
            await kernel.invoke(function, arguments={"article_id": "art"})
        session_id = kernel.session_id

        await kernel.invoke(
            function,
            arguments={"article_id": "art", "session_id": session_id},
        )

        seen = [call.kwargs["session_id"] for call in client.intercept.call_args_list]
        assert seen[1] == session_id, (
            "the resume key must reach the engine as the session id, or "
            "call_signature differs and the engine runs the call again"
        )
        assert _positions(client) == [1, 1], (
            "the retried call must reuse its step, or the engine sees a new "
            "operation instead of replaying the one already journaled"
        )

    async def test_step_key_continues_the_positions(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-2"),
            InterceptResponse(outcome="Execute", effect_id="eff-2"),
        )
        kernel = _kernel_pausing_at_first_call(client)
        function = kernel.plugins["support"]["send_newsletter"]

        with pytest.raises(AwaitingApprovalError):
            await kernel.invoke(function, arguments={"article_id": "art"})
        session_id = kernel.session_id
        step_index = kernel.step_index

        await kernel.invoke(
            function,
            arguments={
                "article_id": "art",
                "session_id": session_id,
                "undolog_step_index": step_index,
            },
        )

        assert _positions(client) == [1, 2], (
            "passing the step key must continue the counter rather than replay it"
        )


# ── Function registration ───────────────────────────────────────────────────


class TestFunctionRegistration:
    """Functions are instrumented on their way into the kernel."""

    async def test_add_function_instruments_before_registering(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.IRREVERSIBLE},
        )

        wrapped.add_function("support", "lookup_customer", _lookup_customer)

        registered = kernel.plugins["support"]["lookup_customer"]
        assert registered is not _lookup_customer, (
            "registration must hand the kernel an instrumented copy"
        )
        assert getattr(registered, "_undolog_tool_name", None), (
            "the registered function must carry UndoLog instrumentation"
        )
        assert getattr(registered, "_undolog_tier", None) is ToolTier.IRREVERSIBLE

    async def test_the_original_function_is_left_untouched(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.IRREVERSIBLE},
        )

        wrapped.add_function("support", "lookup_customer", _lookup_customer)

        assert getattr(_lookup_customer, "_undolog_tool_name", None) is None

    async def test_a_container_is_rejected(self) -> None:
        """A container exposes its callable as ``coroutine``, the shape
        CrewAI's StructuredTool has. Semantic Kernel registers a callable
        it can name and invoke, so this shape is refused rather than
        instrumented on the caller's behalf."""
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")
        container = _Container("search_documents", _search_documents)

        with pytest.raises(ValueError, match="cannot instrument"):
            wrapped.add_function("docs", "search_documents", container)

    async def test_a_nameless_callable_is_rejected(self) -> None:
        """The kernel registers a function under a name, and the journal
        records the tool by it, so a nameless callable is refused."""
        nameless = _nameless_tool()

        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        with pytest.raises(ValueError, match="cannot instrument"):
            wrapped.add_function("docs", "nameless", nameless)

    async def test_registering_a_decorated_function_is_idempotent(self) -> None:
        """A second wrap would journal every call twice."""

        @undolog_tool(tier=ToolTier.SAFE)
        async def search_documents(query: str) -> dict[str, str]:
            return {"query": query}

        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        wrapped.add_function("docs", "search_documents", search_documents)

        assert kernel.plugins["docs"]["search_documents"] is search_documents

    async def test_add_functions_registers_every_function(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={
                "lookup_customer": ToolTier.IRREVERSIBLE,
                "search_documents": ToolTier.SAFE,
            },
        )

        wrapped.add_functions(
            "support",
            {
                "lookup_customer": _lookup_customer,
                "search_documents": _search_documents,
            },
        )

        assert set(kernel.plugins["support"]) == {
            "lookup_customer",
            "search_documents",
        }

    async def test_a_rejected_function_leaves_the_kernel_untouched(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.IRREVERSIBLE},
        )

        with pytest.raises(ValueError, match="cannot instrument"):
            wrapped.add_functions(
                "support",
                {
                    "lookup_customer": _lookup_customer,
                    "sync_search": _sync_search,
                },
            )

        assert kernel.plugins == {}, "nothing may be registered behind a failure"

    async def test_a_sync_function_is_rejected_at_registration(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        with pytest.raises(ValueError, match="async"):
            wrapped.add_function("docs", "sync_search", _sync_search)

    async def test_a_non_callable_is_rejected_at_registration(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        with pytest.raises(ValueError, match="cannot instrument"):
            wrapped.add_function("docs", "blob", object())

    async def test_an_unmapped_compensable_function_is_rejected(self) -> None:
        """A default-tier function with no compensation would run with
        no journal and no approval gate."""
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        with pytest.raises(ValueError, match="compensation"):
            wrapped.add_function("support", "lookup_customer", _lookup_customer)

    async def test_the_tier_decides_whether_the_proxy_is_reached(self) -> None:
        """A SAFE function bypasses the proxy, so no intercept may occur."""
        client = _client()
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"search_documents": ToolTier.SAFE},
            client=client,
        )
        wrapped.add_function("docs", "search_documents", _search_documents)

        result = await wrapped.invoke(
            kernel.plugins["docs"]["search_documents"],
            arguments={"query": "urgent"},
        )

        assert result == {"query": "urgent"}


# ── Approvals ───────────────────────────────────────────────────────────────


class TestApprovalPropagation:
    """An approval suspends the run and leaves it readable on the facade."""

    async def test_awaiting_approval_escalates_to_the_caller(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-3")
        )
        kernel = _kernel_pausing_at_first_call(client)
        function = kernel.plugins["support"]["send_newsletter"]

        with pytest.raises(AwaitingApprovalError) as info:
            await kernel.invoke(function, arguments={"article_id": "art"})

        assert info.value.approval_id == "approval-3"
        assert info.value.tool_name == "send_newsletter"
        assert info.value.step_index == 1

    async def test_invoke_prompt_records_the_run_before_it_escalates(
        self,
    ) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-p")
        )
        kernel = _kernel_pausing_at_first_call(client)

        with pytest.raises(AwaitingApprovalError):
            await kernel.invoke_prompt(
                function_name="send_newsletter",
                plugin_name="support",
                arguments={"article_id": "art"},
            )

        assert kernel.awaiting_approval is True
        assert kernel.approval_request == {
            "approval_id": "approval-p",
            "tool_name": "send_newsletter",
            "step_index": 1,
        }

    async def test_the_run_is_recorded_before_it_escalates(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-4")
        )
        kernel = _kernel_pausing_at_first_call(client)
        function = kernel.plugins["support"]["send_newsletter"]

        with pytest.raises(AwaitingApprovalError):
            await kernel.invoke(function, arguments={"article_id": "art"})

        assert kernel.awaiting_approval is True
        assert kernel.session_id is not None
        assert kernel.approval_request == {
            "approval_id": "approval-4",
            "tool_name": "send_newsletter",
            "step_index": 1,
        }

    async def test_the_facade_resets_before_the_next_run(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-5"),
            InterceptResponse(outcome="Execute", effect_id="eff-5"),
        )
        kernel = _kernel_pausing_at_first_call(client)
        function = kernel.plugins["support"]["send_newsletter"]

        with pytest.raises(AwaitingApprovalError):
            await kernel.invoke(function, arguments={"article_id": "art"})
        await kernel.invoke(
            function,
            arguments={"article_id": "art", "session_id": kernel.session_id},
        )

        assert kernel.awaiting_approval is False
        assert kernel.approval_request is None


# ── Delegation and configuration ────────────────────────────────────────────


class TestDelegation:
    """The facade adds an entry point and the run properties, nothing else."""

    async def test_unknown_attributes_resolve_on_the_kernel(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        assert wrapped.plugins is kernel.plugins

    async def test_private_attributes_are_not_delegated(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_test")

        with pytest.raises(AttributeError):
            wrapped._kernel.typo


class TestOrgResolution:
    """``org_id`` resolves from the argument, then the environment."""

    async def test_explicit_argument(self) -> None:
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(kernel, org_id="org_explicit")

        assert wrapped.org_id == "org_explicit"

    async def test_environment_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UNDOLOG_ORG_ID", "org_env")

        wrapped = wrap_semantic_kernel(FakeKernel())

        assert wrapped.org_id == "org_env"

    async def test_default_when_environment_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UNDOLOG_ORG_ID", raising=False)

        wrapped = wrap_semantic_kernel(FakeKernel())

        assert wrapped.org_id == "org_demo"


class TestSingleLineIntegration:
    """One wrapper call covers registration and invocation."""

    async def test_registration_and_invocation_through_the_facade(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-9"))
        kernel = FakeKernel()
        wrapped = wrap_semantic_kernel(
            kernel,
            org_id="org_test",
            tiers={"lookup_customer": ToolTier.IRREVERSIBLE},
            client=client,
        )

        wrapped.add_function("support", "lookup_customer", _lookup_customer)
        result = await wrapped.invoke(
            kernel.plugins["support"]["lookup_customer"],
            arguments={"customer_id": "cust_42"},
        )

        assert result == {"customer_id": "cust_42"}
        assert _positions(client) == [1]
        assert wrapped.session_id is not None


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


def _kernel_pausing_at_first_call(client: AsyncMock) -> WrappedKernel:
    """Build a kernel whose one plugin function waits for approval.

    Parameters:
        client: UndoLog client stub answering that function's
            ``intercept`` calls.

    Returns:
        A wrapped kernel with the function already registered, ready to
        invoke.
    """

    async def send_newsletter(article_id: str) -> str:
        return article_id

    kernel = FakeKernel()
    wrapped = wrap_semantic_kernel(
        kernel,
        org_id="org_test",
        tiers={"send_newsletter": ToolTier.IRREVERSIBLE},
        client=client,
    )
    wrapped.add_function("support", "send_newsletter", send_newsletter)
    return wrapped


def _positions(client: AsyncMock) -> list[int]:
    """Step index of every ``intercept`` call, in call order.

    Parameters:
        client: UndoLog client stub whose calls are recorded.

    Returns:
        The ``step_index`` each call was made with.
    """
    return [call.kwargs["step_index"] for call in client.intercept.call_args_list]


class _sealed:
    """Read-only view of a mapping, as an immutable arguments mapping."""

    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping

    def __contains__(self, key: object) -> bool:
        return key in self._mapping

    def __getitem__(self, key: str) -> Any:
        return self._mapping[key]

    def pop(self, key: str, default: Any = None) -> Any:
        raise TypeError("mapping is read-only")
