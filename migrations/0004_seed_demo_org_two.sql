-- Seed a second demo organisation for multi-tenant demos.
--
-- This migration creates a second tenant (org-beta) with the same tool
-- registrations as org-alpha so that concurrent multi-org demos can
-- demonstrate tenant isolation without sharing tool definitions.
--
-- Idempotent: all INSERTs use ON CONFLICT DO NOTHING.

INSERT INTO undolog_orgs (org_id, slug, name, created_at)
VALUES (
    '00000000-0000-0000-0000-000000000002',
    'org-beta',
    'Beta Organisation',
    now()
)
ON CONFLICT (org_id) DO NOTHING;

INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000201',
    '00000000-0000-0000-0000-000000000002',
    'charge_payment',
    '1.0.0',
    'compensable',
    'compensate_charge_payment',
    '{"type":"object","properties":{"amount":{"type":"number"}}}'::jsonb,
    true,
    '{}',
    'Charges a payment to the customer primary payment method',
    now(),
    now()
)
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;

INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000202',
    '00000000-0000-0000-0000-000000000002',
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

INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000203',
    '00000000-0000-0000-0000-000000000002',
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

INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    irreversibility_reason, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000204',
    '00000000-0000-0000-0000-000000000002',
    'escalate_case',
    '1.0.0',
    'irreversible',
    'Escalation cannot be undone once submitted to the priority queue',
    '{"type":"object","properties":{"case_id":{"type":"string"},"reason":{"type":"string"}}}'::jsonb,
    true,
    '{high-risk,billing}',
    'Escalates a customer case to the priority support queue -- cannot be reversed',
    now(),
    now()
)
ON CONFLICT (org_id, tool_name, tool_version)
    WHERE is_active = true
DO NOTHING;

INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000205',
    '00000000-0000-0000-0000-000000000002',
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

INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000206',
    '00000000-0000-0000-0000-000000000002',
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

INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000207',
    '00000000-0000-0000-0000-000000000002',
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

INSERT INTO undolog_tool_registry (
    tool_id, org_id, tool_name, tool_version, tier,
    compensation_ref, tool_schema, is_active, risk_tags, estimated_impact,
    registered_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000208',
    '00000000-0000-0000-0000-000000000002',
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
