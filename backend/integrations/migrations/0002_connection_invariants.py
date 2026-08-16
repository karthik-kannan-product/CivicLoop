# ruff: noqa: E501, I001
from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


CREATE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION integrations_enforce_connection_invariants()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    secret_provider varchar(32);
    secret_status varchar(16);
BEGIN
    IF jsonb_typeof(NEW.configuration) <> 'object' OR jsonb_typeof(NEW.capabilities) <> 'array' THEN
        RAISE EXCEPTION 'Integration connection invariants are invalid.' USING ERRCODE = '23514';
    END IF;

    IF (NEW.provider = 'eventbrite' AND NEW.configuration <> '{}'::jsonb)
        OR (NEW.provider = 'iterable' AND NEW.configuration NOT IN ('{}'::jsonb, '{"region":"us"}'::jsonb, '{"region":"eu"}'::jsonb))
        OR (NEW.provider IN ('openai', 'groq') AND NEW.configuration NOT IN ('{}'::jsonb, '{"model":"openai/gpt-oss-20b"}'::jsonb)) THEN
        RAISE EXCEPTION 'Integration connection invariants are invalid.' USING ERRCODE = '23514';
    END IF;

    IF NEW.state = 'not_configured' THEN
        IF NEW.secret_id IS NOT NULL OR NEW.capabilities <> '[]'::jsonb THEN
            RAISE EXCEPTION 'Integration connection invariants are invalid.' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.state NOT IN ('configured', 'healthy', 'degraded', 'disabled') OR NEW.secret_id IS NULL THEN
        RAISE EXCEPTION 'Integration connection invariants are invalid.' USING ERRCODE = '23514';
    END IF;

    SELECT provider, status INTO secret_provider, secret_status
    FROM integrations_encryptedsecret WHERE id = NEW.secret_id;
    IF secret_provider IS DISTINCT FROM NEW.provider
        OR (NEW.state = 'disabled' AND secret_status <> 'disabled')
        OR (NEW.state <> 'disabled' AND secret_status <> 'active') THEN
        RAISE EXCEPTION 'Integration connection invariants are invalid.' USING ERRCODE = '23514';
    END IF;

    IF (NEW.provider IN ('eventbrite', 'iterable') AND NEW.capabilities <> '["connection_test","draft_create","metadata_read"]'::jsonb)
        OR (NEW.provider IN ('openai', 'groq') AND NEW.capabilities <> '["connection_test","evaluation_judge","inference"]'::jsonb) THEN
        RAISE EXCEPTION 'Integration connection invariants are invalid.' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

CREATE_TRIGGER_SQL = """
CREATE TRIGGER integrations_connection_invariants
BEFORE INSERT OR UPDATE ON integrations_integrationconnection
FOR EACH ROW EXECUTE FUNCTION integrations_enforce_connection_invariants();
"""

DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS integrations_connection_invariants ON integrations_integrationconnection;
DROP FUNCTION IF EXISTS integrations_enforce_connection_invariants();
"""


def create_connection_invariant_trigger(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(CREATE_FUNCTION_SQL)
    schema_editor.execute(CREATE_TRIGGER_SQL)


def drop_connection_invariant_trigger(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(DROP_TRIGGER_SQL)


class Migration(migrations.Migration):
    dependencies = [("integrations", "0001_initial")]

    operations = [
        migrations.RunPython(
            create_connection_invariant_trigger,
            reverse_code=drop_connection_invariant_trigger,
        )
    ]
