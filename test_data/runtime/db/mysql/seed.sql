-- Synthetic MySQL seed rows. Test infrastructure only.

INSERT INTO managed_targets (target_id, tenant_id, status, row_version, updated_at) VALUES
    ('target-acme-000001', 'tenant-acme', 'active', 1, '2026-08-26 00:00:00'),
    ('target-beta-000001', 'tenant-beta', 'active', 1, '2026-08-26 00:00:00');

INSERT INTO refresh_source_rows (row_id, source_id, resource, watermark, sequence_no, payload_summary) VALUES
    ('row-acme-000001', 'source-acme-erp', 'purchase_orders', '2026-08-25 22:00:00', 1, 'normal batch row'),
    ('row-acme-000002', 'source-acme-erp', 'purchase_orders', '2026-08-25 23:00:00', 2, 'late-arriving row'),
    ('row-beta-000001', 'source-beta-crm', 'accounts', '2026-08-25 22:30:00', 1, 'normal batch row');

INSERT INTO refresh_event_log (event_id, source_id, resource, cursor_value, sequence_no, received_at) VALUES
    ('event-acme-000001', 'source-acme-erp', 'purchase_orders', 'cursor-acme-000001', 1, '2026-08-25 22:05:00'),
    ('event-beta-000001', 'source-beta-crm', 'accounts', 'cursor-beta-000001', 1, '2026-08-25 22:35:00');
