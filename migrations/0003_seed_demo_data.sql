-- Seed demo organisation and tool registrations.
--
-- These rows are required by the example demos (approval_demo.py,
-- compensation_demo.py, replay_demo.py) and the LangChain agent example
-- (run_against_stack.py).
--
-- Idempotent: all INSERTs use ON CONFLICT DO NOTHING so this migration
-- is safe to apply repeatedly.

INSERT INTO undolog_orgs (org_id, slug, name, created_at)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'org-demo',
    'Demo Organisation',
    now()
)
ON CONFLICT (org_id) DO NOTHING;

-- charge_payment: COMPENSABLE (used by replay_demo.py)
INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
    )
    VALUES (
        '00000000-0000-0000-0000-000000000101',
        '00000000-0000-0000-0000-000000000001',
        'charge_payment',
        '1.0.0',
        'compensable',
        'compensate_charge_payment',
        '{"type":"object","properties":{"amount":{"type":"number"}}}'::jsonb,
        true,
        '{}',
        'Charges a payment to the customer''s primary payment method',
        now(),
        now()
    )
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;

-- send_email: COMPENSABLE (used by tools.py / run_against_stack.py)
INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000102',
    '00000000-0000-0000-0000-000000000001',
    'send_email',
    '1.0.0',
    'compensable',
    'compensate_send_email',
    '{"type":"object","properties":{"to":{"type":"string"},"subject":{"type":"string"}}}'::jsonb,
    true,
    '{}',
    'Sends transactional email to a customer',
    now(),
    now()
)
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;

-- create_ticket: COMPENSABLE (used by tools.py / run_against_stack.py)
INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000103',
    '00000000-0000-0000-0000-000000000001',
    'create_ticket',
    '1.0.0',
    'compensable',
    'compensate_create_ticket',
    '{"type":"object","properties":{"title":{"type":"string"},"description":{"type":"string"}}}'::jsonb,
    true,
    '{}',
    'Creates a customer support ticket in the ticketing system',
    now(),
    now()
)
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;

-- escalate_case: IRREVERSIBLE (used by tools.py / run_against_stack.py)
INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    irreversibility_reason, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000104',
    '00000000-0000-0000-0000-000000000001',
    'escalate_case',
    '1.0.0',
    'irreversible',
    'Escalation cannot be undone once submitted to the priority queue',
    '{"type":"object","properties":{"case_id":{"type":"string"},"reason":{"type":"string"}}}'::jsonb,
    true,
    '{high-risk,billing}',
    'Escalates a customer case to the priority support queue -- '
    'cannot be reversed',
    now(),
    now()
)
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;

-- notify_user: COMPENSABLE (used by compensation_demo.py)
INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000105',
    '00000000-0000-0000-0000-000000000001',
    'notify_user',
    '1.0.0',
    'compensable',
    'compensate_send_email',
    '{"type":"object","properties":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}}}'::jsonb,
    true,
    '{}',
    'Sends a notification email to a user',
    now(),
    now()
)
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;

-- open_ticket: COMPENSABLE (used by compensation_demo.py)
INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000106',
    '00000000-0000-0000-0000-000000000001',
    'open_ticket',
    '1.0.0',
    'compensable',
    'compensate_create_ticket',
    '{"type":"object","properties":{"user_id":{"type":"string"},"priority":{"type":"string"},"description":{"type":"string"}}}'::jsonb,
    true,
    '{}',
    'Opens a new support ticket',
    now(),
    now()
)
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;

-- assign_engineer: COMPENSABLE (used by compensation_demo.py)
INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000107',
    '00000000-0000-0000-0000-000000000001',
    'assign_engineer',
    '1.0.0',
    'compensable',
    'compensate_assign_engineer',
    '{"type":"object","properties":{"ticket_id":{"type":"string"},"engineer":{"type":"string"}}}'::jsonb,
    true,
    '{}',
    'Assigns an engineer to a ticket',
    now(),
    now()
)
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;

-- escalate_ticket: COMPENSABLE (used by compensation_demo.py)
INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000108',
    '00000000-0000-0000-0000-000000000001',
    'escalate_ticket',
    '1.0.0',
    'compensable',
    'compensate_escalate',
    '{"type":"object","properties":{"ticket_id":{"type":"string"},"reason":{"type":"string"}}}'::jsonb,
    true,
    '{}',
    'Escalates a ticket to senior support',
    now(),
    now()
)
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;
