"use client";

import { useState } from "react";
import { KW, FN, ST, CM } from "./Syntax";
import CopyButton from "./CopyButton";
import TierInspectorCard from "./TierInspectorCard";

const agentPy = `# agent.py — Powered by UndoLog

from undolog_sdk import undolog_tool, ToolTier
from undolog_sdk.tier import CompensationDescriptor

# SAFE — read-only · no log entry · replayed on retry
@undolog_tool(tier=ToolTier.SAFE)
async def lookup_customer(customer_id: str) -> dict:
    return {"name": ..., "plan": ...}

# COMPENSABLE — writes · logs effect · registers undo
@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new(
        "undo_create_ticket",
        args={"ticket_id": "{ticket_id}"},
    ),
)
async def create_ticket(customer_id: str, amount: float) -> dict:
    await tools.create_ticket(customer_id, amount)

# IRREVERSIBLE — approval gate · session suspended
@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def send_email(to: str, subject: str, body: str) -> dict:
    return await mailer.send(to, subject, body)`;

const toolsPy = `# tools.py — Tool implementations

from undolog_sdk.tier import Tier, compensable
from undolog_sdk.compensation import undo, EffectLog

# Mock customer DB — SAFE tier
async def lookup_customer(cid: str) -> dict:
    rows = await db.query("SELECT * FROM customers WHERE id = :cid")
    if not rows:
        raise ValueError("customer not found")
    return rows[0]

# Ticket creation — COMPENSABLE tier
async def create_ticket(customer: str, amount: float) -> dict:
    ticket = await db.insert("tickets", {customer, amount})
    await EffectLog.record("ticket.created", ticket.id)
    return ticket

# Undo handler for ticket creation
async def undo_create_ticket(ticket_id: str) -> None:
    await db.delete("tickets", {"id": ticket_id})
    await EffectLog.record("ticket.deleted", ticket_id)`;

const sessionPy = `# session.py — Session lifecycle

from undolog_sdk import Session, ApprovalGate
from undolog_sdk.compensation import Saga
from undolog_sdk.effects import EffectJournal

# Initialize session with tier context
session = Session(org_id="org_demo")
journal = EffectJournal(session_id=session.id)

# Register saga for LIFO rollback
saga = Saga(session_id=session.id)
saga.on_failure.connect(rollback_handler)

# On IRREVERSIBLE call → suspend
async def handle_irreversible(tool_name: str, ctx: dict) -> None:
    approval_id = generate_id()
    await session.suspend(
        reason="IRREVERSIBLE tool: send_email",
        approval_id="apr_9f8e",
        risk_tags=["external_communication", "financial_data"],
        gate=ApprovalGate(session_id=session.id),
    )
    # waiting for human judgment...`;

const LINE_DATA: Record<string, { lines: React.ReactNode[]; total: number; source: string }> = {
  "agent.py": {
    total: 25,
    source: agentPy,
    lines: [
      <CM># agent.py — Powered by UndoLog</CM>,
      <>&nbsp;</>,
      <><KW>from</KW> undolog_sdk <KW>import</KW> undolog_tool, ToolTier</>,
      <><KW>from</KW> undolog_sdk.tier <KW>import</KW> CompensationDescriptor</>,
      <>&nbsp;</>,
      <CM># SAFE — read-only · no log entry · replayed on retry</CM>,
      <><KW>@</KW><FN>undolog_tool</FN>(tier=ToolTier.SAFE)</>,
      <><KW>async def</KW> <FN>lookup_customer</FN>(customer_id: <KW>str</KW>) → <KW>dict</KW>:</>,
      <span style={{ paddingLeft: 16 }}><KW>return</KW> {"{"}name: ..., plan: ...{"}"}</span>,
      <>&nbsp;</>,
      <CM># COMPENSABLE — writes · logs effect · registers undo</CM>,
      <><KW>@</KW><FN>undolog_tool</FN>(</>,
      <span style={{ paddingLeft: 16 }}>tier=ToolTier.COMPENSABLE,</span>,
      <span style={{ paddingLeft: 16 }}>compensation=CompensationDescriptor.<FN>new</FN>(</span>,
      <span style={{ paddingLeft: 32 }}><ST>"undo_create_ticket"</ST>,</span>,
      <span style={{ paddingLeft: 32 }}>{'args={{"ticket_id": "{ticket_id}"}},'}</span>,
      <span style={{ paddingLeft: 16 }}>),</span>,
      <>)</>,
      <><KW>async def</KW> <FN>create_ticket</FN>(customer_id: <KW>str</KW>, amount: <KW>float</KW>) → <KW>dict</KW>:</>,
      <span style={{ paddingLeft: 16 }}><KW>await</KW> tools.create_ticket(customer_id, amount)</span>,
      <>&nbsp;</>,
      <CM># IRREVERSIBLE — approval gate · session suspended</CM>,
      <><KW>@</KW><FN>undolog_tool</FN>(tier=ToolTier.IRREVERSIBLE)</>,
      <><KW>async def</KW> <FN>send_email</FN>(to: <KW>str</KW>, subject: <KW>str</KW>, body: <KW>str</KW>) → <KW>dict</KW>:</>,
      <span style={{ paddingLeft: 16 }}><KW>return</KW> <KW>await</KW> mailer.send(to, subject, body)</span>,
    ],
  },
  "tools.py": {
    total: 22,
    source: toolsPy,
    lines: [
      <CM># tools.py — Tool implementations</CM>,
      <>&nbsp;</>,
      <><KW>from</KW> undolog_sdk.tier <KW>import</KW> Tier, compensable</>,
      <><KW>from</KW> undolog_sdk.compensation <KW>import</KW> undo, EffectLog</>,
      <>&nbsp;</>,
      <CM># Mock customer DB — SAFE tier</CM>,
      <><KW>async def</KW> <FN>lookup_customer</FN>(cid: <KW>str</KW>) → <KW>dict</KW>:</>,
      <span style={{ paddingLeft: 16 }}>rows = <KW>await</KW> db.query(<ST>"SELECT * FROM customers WHERE id = :cid"</ST>)</span>,
      <span style={{ paddingLeft: 16 }}><KW>if</KW> <KW>not</KW> rows:</span>,
      <span style={{ paddingLeft: 32 }}><KW>raise</KW> ValueError(<ST>"customer not found"</ST>)</span>,
      <span style={{ paddingLeft: 16 }}><KW>return</KW> rows[0]</span>,
      <>&nbsp;</>,
      <CM># Ticket creation — COMPENSABLE tier</CM>,
      <><KW>async def</KW> <FN>create_ticket</FN>(customer: <KW>str</KW>, amount: <KW>float</KW>) → <KW>dict</KW>:</>,
      <span style={{ paddingLeft: 16 }}>ticket = <KW>await</KW> db.insert(<ST>"tickets"</ST>, {"{"}customer, amount{"}"})</span>,
      <span style={{ paddingLeft: 16 }}><KW>await</KW> EffectLog.record(<ST>"ticket.created"</ST>, ticket.id)</span>,
      <span style={{ paddingLeft: 16 }}><KW>return</KW> ticket</span>,
      <>&nbsp;</>,
      <CM># Undo handler for ticket creation</CM>,
      <><KW>async def</KW> <FN>undo_create_ticket</FN>(ticket_id: <KW>str</KW>) → <KW>None</KW>:</>,
      <span style={{ paddingLeft: 16 }}><KW>await</KW> db.delete(<ST>"tickets"</ST>, {"{"}<ST>"id"</ST>: ticket_id{"}"})</span>,
      <span style={{ paddingLeft: 16 }}><KW>await</KW> EffectLog.record(<ST>"ticket.deleted"</ST>, ticket_id)</span>,
    ],
  },
  "session.py": {
    total: 24,
    source: sessionPy,
    lines: [
      <CM># session.py — Session lifecycle</CM>,
      <>&nbsp;</>,
      <><KW>from</KW> undolog_sdk <KW>import</KW> Session, ApprovalGate</>,
      <><KW>from</KW> undolog_sdk.compensation <KW>import</KW> Saga</>,
      <><KW>from</KW> undolog_sdk.effects <KW>import</KW> EffectJournal</>,
      <>&nbsp;</>,
      <CM># Initialize session with tier context</CM>,
      <>session = Session(org_id=<ST>"org_demo"</ST>)</>,
      <>journal = EffectJournal(session_id=session.id)</>,
      <>&nbsp;</>,
      <CM># Register saga for LIFO rollback</CM>,
      <>saga = Saga(session_id=session.id)</>,
      <>saga.on_failure.connect(<FN>rollback_handler</FN>)</>,
      <>&nbsp;</>,
      <CM># On IRREVERSIBLE call → suspend</CM>,
      <><KW>async def</KW> <FN>handle_irreversible</FN>(tool_name: <KW>str</KW>, ctx: <KW>dict</KW>) → <KW>None</KW>:</>,
      <span style={{ paddingLeft: 16 }}>approval_id = <FN>generate_id</FN>()</span>,
      <span style={{ paddingLeft: 16 }}><KW>await</KW> session.suspend(</span>,
      <span style={{ paddingLeft: 32 }}>reason=<ST>"IRREVERSIBLE tool: send_email"</ST>,</span>,
      <span style={{ paddingLeft: 32 }}>approval_id=<ST>"apr_9f8e"</ST>,</span>,
      <span style={{ paddingLeft: 32 }}>risk_tags=[<ST>"external_communication"</ST>, <ST>"financial_data"</ST>],</span>,
      <span style={{ paddingLeft: 16 }}>gate=<FN>ApprovalGate</FN>(session_id=session.id),</span>,
      <>)</>,
      <CM># waiting for human judgment...</CM>,
    ],
  },
};

/**
 * DecoratorShowcase: Interactive code-tabbed display with three file tabs
 * (agent.py, tools.py, session.py) showing the decorator-based tier annotation
 * pattern. Includes a floating Tier Inspector card explaining each tier.
 */
export default function DecoratorShowcase() {
  const [fileTab, setFileTab] = useState("agent.py");

  const activeFile = LINE_DATA[fileTab];
  const lineCount = activeFile.total;

  return (
    <div style={{ position: "relative" }}>
      <div className="code-showcase-block">
        <div className="code-showcase-header">
          <div style={{ display: "flex", gap: 5, marginRight: 4 }}>
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#FF5F57" }} />
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#FEBC2E" }} />
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#28C840" }} />
          </div>
          {["agent.py", "tools.py", "session.py"].map((name) => (
            <div
              key={name}
              onClick={() => setFileTab(name)}
              style={{
                fontFamily: "'Geist', sans-serif",
                fontSize: 12,
                color: fileTab === name ? "#FFFFFF" : "rgba(255,255,255,0.2)",
                borderBottom: fileTab === name ? "2px solid var(--purple-primary)" : "2px solid transparent",
                background: fileTab === name ? "rgba(255,255,255,0.02)" : "transparent",
                cursor: "pointer",
                userSelect: "none",
              }}
            >
              {name}
            </div>
          ))}
          <div style={{ flex: 1 }} />
          <CopyButton code={activeFile.source} className="code-showcase-copy" />
        </div>

        <pre className="code-showcase-pre">
          {activeFile.lines.map((line, i) => (
            <div key={i} className="code-showcase-line">
              <span className="code-showcase-ln">{i + 1}</span>
              <span className="code-showcase-content">{line}</span>
            </div>
          ))}
          <div className="code-showcase-line">
            <span className="code-showcase-ln">{lineCount + 1}</span>
            <span className="code-showcase-content">
              <span className="code-showcase-cursor" />
            </span>
          </div>
        </pre>

        <div className="code-showcase-status">
          <span>Python 3.12</span>
          <span>UTF-8</span>
          <span>Ln {lineCount + 1}, Col 1</span>
        </div>
      </div>

      <TierInspectorCard />
    </div>
  );
}
