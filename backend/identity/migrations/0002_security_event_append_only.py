from django.db import migrations

CREATE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION identity_reject_security_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Administrator security events are append-only.'
        USING ERRCODE = '55000';
END;
$$;
"""

CREATE_TRIGGER_SQL = """
CREATE TRIGGER identity_security_event_append_only
BEFORE UPDATE OR DELETE ON identity_administratorsecurityevent
FOR EACH ROW
EXECUTE FUNCTION identity_reject_security_event_mutation();
"""

DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS identity_security_event_append_only
ON identity_administratorsecurityevent;
"""

DROP_FUNCTION_SQL = """
DROP FUNCTION IF EXISTS identity_reject_security_event_mutation();
"""


def create_append_only_trigger(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(CREATE_FUNCTION_SQL)
    schema_editor.execute(CREATE_TRIGGER_SQL)


def drop_append_only_trigger(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(DROP_TRIGGER_SQL)
    schema_editor.execute(DROP_FUNCTION_SQL)


class Migration(migrations.Migration):
    dependencies = [("identity", "0001_initial")]

    operations = [
        migrations.RunPython(
            create_append_only_trigger,
            reverse_code=drop_append_only_trigger,
        )
    ]
