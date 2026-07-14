"""完整基线 — 全部 v1 + v2 表 (压缩自损坏的 0002-0004 迁移链)

此前迁移链断裂 (0004 down_revision 指向不存在的 revision ID) 且缺 v1 全部表与
PRD v1.1 的 logic/action 表, alembic upgrade head 从未可用; 开发库一直由
启动时 create_all 管理, 无任何已 stamp 的环境, 故安全压缩为单一基线。

Revision ID: 0001_full_baseline
Revises:
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_full_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('rules_config',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('rule_key', sa.String(length=100), nullable=False),
    sa.Column('rule_value', sa.String(length=200), nullable=False),
    sa.Column('rule_label_cn', sa.String(length=200), nullable=False),
    sa.Column('rule_label_en', sa.String(length=200), nullable=False),
    sa.Column('editable', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rule_key')
    )
    op.create_table('users',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False),
    sa.Column('email', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.String(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email'),
    sa.UniqueConstraint('username')
    )
    op.create_table('model_configs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('config_type', sa.String(length=30), nullable=False),
    sa.Column('api_base', sa.String(length=500), nullable=True),
    sa.Column('api_key_encrypted', sa.Text(), nullable=True),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('models', sa.JSON(), nullable=False),
    sa.Column('options', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('ontology_projects',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('domain', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('build_mode', sa.String(length=30), nullable=True),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('prompts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('domain', sa.String(length=100), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_connections',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=50), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('actions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('name_cn', sa.String(length=200), nullable=False),
    sa.Column('name_en', sa.String(length=200), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('execution_rule', sa.Text(), nullable=True),
    sa.Column('function_code', sa.Text(), nullable=True),
    sa.Column('linked_entities', sa.JSON(), nullable=False),
    sa.Column('linked_logic_ids', sa.JSON(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('entities',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('name_cn', sa.String(length=200), nullable=False),
    sa.Column('name_en', sa.String(length=200), nullable=True),
    sa.Column('type', sa.String(length=100), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('properties', sa.JSON(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('extraction_tasks',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('prompt_id', sa.String(), nullable=True),
    sa.Column('model_id', sa.String(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('parameters', sa.JSON(), nullable=False),
    sa.Column('progress', sa.JSON(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('validation_report', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['model_id'], ['model_configs.id'], ),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['prompt_id'], ['prompts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('logic_rules',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('name_cn', sa.String(length=200), nullable=False),
    sa.Column('name_en', sa.String(length=200), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('formula', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('linked_entities', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('uploaded_files',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('filename', sa.String(length=500), nullable=False),
    sa.Column('file_path', sa.String(length=1000), nullable=False),
    sa.Column('file_size', sa.Integer(), nullable=False),
    sa.Column('mime_type', sa.String(length=200), nullable=True),
    sa.Column('converted_md', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_datasets',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('source_connection_id', sa.String(), nullable=True),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('schema_json', sa.JSON(), nullable=True),
    sa.Column('latest_version_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_connection_id'], ['v2_connections.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_ontology_action_types',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('target_entity_type', sa.String(length=200), nullable=True),
    sa.Column('action_category', sa.String(length=50), nullable=False),
    sa.Column('parameters', sa.JSON(), nullable=False),
    sa.Column('submission_criteria', sa.JSON(), nullable=True),
    sa.Column('effects', sa.JSON(), nullable=False),
    sa.Column('side_effects', sa.JSON(), nullable=True),
    sa.Column('permission_rules', sa.JSON(), nullable=True),
    sa.Column('backed_by_function', sa.String(length=200), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_ontology_logic_rules',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('logic_type', sa.String(length=50), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('target_entity_type', sa.String(length=200), nullable=True),
    sa.Column('expression', sa.JSON(), nullable=False),
    sa.Column('source_type', sa.String(length=50), nullable=True),
    sa.Column('source_ref', sa.JSON(), nullable=True),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_ontology_state_machines',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('entity_type_name', sa.String(length=200), nullable=False),
    sa.Column('state_property', sa.String(length=100), nullable=False),
    sa.Column('states', sa.JSON(), nullable=False),
    sa.Column('transitions', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('relations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('source_entity', sa.String(), nullable=False),
    sa.Column('target_entity', sa.String(), nullable=False),
    sa.Column('type', sa.String(length=100), nullable=False),
    sa.Column('properties', sa.JSON(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_entity'], ['entities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_entity'], ['entities.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_dataset_versions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('dataset_id', sa.String(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('rowcount', sa.BigInteger(), nullable=True),
    sa.Column('storage_uri', sa.Text(), nullable=True),
    sa.Column('checksum', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['v2_datasets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_ontology_action_runs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('action_type_id', sa.String(), nullable=True),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('target_object_id', sa.String(length=200), nullable=True),
    sa.Column('parameters', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('before_snapshot', sa.JSON(), nullable=True),
    sa.Column('after_snapshot', sa.JSON(), nullable=True),
    sa.Column('side_effect_results', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('executed_by', sa.String(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['action_type_id'], ['v2_ontology_action_types.id'], ),
    sa.ForeignKeyConstraint(['executed_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_pipelines',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('domain', sa.String(length=100), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('source_dataset_id', sa.String(), nullable=True),
    sa.Column('route', sa.String(length=1), nullable=True),
    sa.Column('spec', sa.JSON(), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=True),
    sa.Column('target_curated_ids', sa.JSON(), nullable=True),
    sa.Column('schedule_cron', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('branch', sa.String(length=50), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['source_dataset_id'], ['v2_datasets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_curated_datasets',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('pipeline_id', sa.String(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('schema_json', sa.JSON(), nullable=True),
    sa.Column('latest_version_id', sa.String(), nullable=True),
    sa.Column('quality_score', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['pipeline_id'], ['v2_pipelines.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_media_items',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('dataset_version_id', sa.String(), nullable=False),
    sa.Column('media_type', sa.String(length=20), nullable=False),
    sa.Column('storage_uri', sa.Text(), nullable=False),
    sa.Column('ocr_status', sa.String(length=20), nullable=False),
    sa.Column('ocr_result_uri', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['dataset_version_id'], ['v2_dataset_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_pipeline_runs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('pipeline_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('stats', sa.JSON(), nullable=True),
    sa.Column('error_log', sa.Text(), nullable=True),
    sa.Column('dataset_version_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['dataset_version_id'], ['v2_dataset_versions.id'], ),
    sa.ForeignKeyConstraint(['pipeline_id'], ['v2_pipelines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_pipeline_versions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('pipeline_id', sa.String(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['pipeline_id'], ['v2_pipelines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_curated_reviews',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('curated_dataset_id', sa.String(), nullable=False),
    sa.Column('reviewer_id', sa.String(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['curated_dataset_id'], ['v2_curated_datasets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_ontology_link_mappings',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('src_dataset_id', sa.String(), nullable=True),
    sa.Column('tgt_dataset_id', sa.String(), nullable=True),
    sa.Column('relation_type', sa.String(length=100), nullable=False),
    sa.Column('src_key', sa.String(length=100), nullable=False),
    sa.Column('tgt_key', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['src_dataset_id'], ['v2_curated_datasets.id'], ),
    sa.ForeignKeyConstraint(['tgt_dataset_id'], ['v2_curated_datasets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_ontology_mappings',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ontology_id', sa.String(), nullable=False),
    sa.Column('curated_dataset_id', sa.String(), nullable=True),
    sa.Column('entity_class', sa.String(length=200), nullable=False),
    sa.Column('field_mapping', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['curated_dataset_id'], ['v2_curated_datasets.id'], ),
    sa.ForeignKeyConstraint(['ontology_id'], ['ontology_projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('v2_curated_row_edits',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('review_id', sa.String(), nullable=False),
    sa.Column('row_pk', sa.String(length=200), nullable=False),
    sa.Column('field_name', sa.String(length=200), nullable=False),
    sa.Column('old_value', sa.Text(), nullable=True),
    sa.Column('new_value', sa.Text(), nullable=True),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['review_id'], ['v2_curated_reviews.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_table('v2_curated_row_edits')
    op.drop_table('v2_ontology_mappings')
    op.drop_table('v2_ontology_link_mappings')
    op.drop_table('v2_curated_reviews')
    op.drop_table('v2_pipeline_versions')
    op.drop_table('v2_pipeline_runs')
    op.drop_table('v2_media_items')
    op.drop_table('v2_curated_datasets')
    op.drop_table('v2_pipelines')
    op.drop_table('v2_ontology_action_runs')
    op.drop_table('v2_dataset_versions')
    op.drop_table('relations')
    op.drop_table('v2_ontology_state_machines')
    op.drop_table('v2_ontology_logic_rules')
    op.drop_table('v2_ontology_action_types')
    op.drop_table('v2_datasets')
    op.drop_table('uploaded_files')
    op.drop_table('logic_rules')
    op.drop_table('extraction_tasks')
    op.drop_table('entities')
    op.drop_table('actions')
    op.drop_table('v2_connections')
    op.drop_table('prompts')
    op.drop_table('ontology_projects')
    op.drop_table('model_configs')
    op.drop_table('users')
    op.drop_table('rules_config')
