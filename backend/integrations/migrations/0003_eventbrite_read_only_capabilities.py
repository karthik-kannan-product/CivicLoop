# ruff: noqa: E501
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
    IF (NEW.provider = 'eventbrite' AND NEW.capabilities <> '["connection_test","metadata_read"]'::jsonb)
        OR (NEW.provider = 'iterable' AND NEW.capabilities <> '["connection_test","draft_create","metadata_read"]'::jsonb)
        OR (NEW.provider IN ('openai', 'groq') AND NEW.capabilities <> '["connection_test","evaluation_judge","inference"]'::jsonb) THEN
        RAISE EXCEPTION 'Integration connection invariants are invalid.' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
"""


def forwards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    connection = apps.get_model("integrations", "IntegrationConnection")
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE_FUNCTION_SQL)
    connection.objects.filter(provider="eventbrite").exclude(state="not_configured").update(
        capabilities=["connection_test", "metadata_read"]
    )


class Migration(migrations.Migration):
    dependencies = [("integrations", "0002_connection_invariants")]
    operations = [migrations.RunPython(forwards, reverse_code=migrations.RunPython.noop)]
