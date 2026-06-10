"""
LangChain agent using UndoLog for exactly-once tool execution.

The agent drives a customer-support workflow through four UndoLog-wrapped
tools (SAFE / COMPENSABLE / IRREVERSIBLE).  Every tool call is recorded
in the effect log so that crashes, replays, and rollbacks are handled
deterministically.
"""

from __future__ import annotations

import asyncio
import os

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from undolog_sdk.session import UndoLogSession

from tools import get_tool_registry


async def main() -> None:
    """Run the LangChain agent inside an UndoLog session.

    Expects
    -------
    UNDOLOG_PROXY_URL : str
        HTTP address of the UndoLog proxy (default ``http://localhost:8080``).
    OPENAI_API_KEY : str
        API key for the OpenAI-compatible LLM provider.
    UNDOLOG_ORG_ID : str
        Organisation identifier (default ``org_demo``).

    Notes
    -----
    The agent uses ``gpt-4o`` by default.  Override with ``OPENAI_MODEL``
    and ``OPENAI_BASE_URL`` to use any OpenAI-compatible provider.
    """
    proxy_url = os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key:
        print("ERROR: Set OPENAI_API_KEY environment variable")
        return

    org_id = os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    base_url = os.environ.get("OPENAI_BASE_URL")

    print(f"Connecting to UndoLog proxy at {proxy_url} ...")

    tools = list(get_tool_registry().values())

    llm = ChatOpenAI(model=model, api_key=openai_api_key, base_url=base_url)
    agent = create_react_agent(llm, tools)

    async with UndoLogSession(org_id=org_id) as session:
        print(f"\nSession: {session.session_id}")
        print(f"Org:      {org_id}\n")

        messages = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "A premium customer named Alice (cust_42) reports she "
                            "can't access the enterprise dashboard. Look up her info, "
                            "create a support ticket, and send an acknowledgment email."
                        ),
                    }
                ]
            }
        )

        print(f"\nAgent response: {messages['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(main())
