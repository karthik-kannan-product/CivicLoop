from django.db import migrations

CREATE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION evaluations_reject_result_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Evaluation results are append-only.' USING ERRCODE = '55000';
END;
$$;
"""


def create_append_only_trigger(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(CREATE_FUNCTION_SQL)
    schema_editor.execute(
        """
        CREATE TRIGGER evaluations_result_append_only
        BEFORE UPDATE OR DELETE ON evaluations_evaluationresult
        FOR EACH ROW EXECUTE FUNCTION evaluations_reject_result_mutation();
        """
    )


def drop_append_only_trigger(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "DROP TRIGGER IF EXISTS evaluations_result_append_only ON evaluations_evaluationresult;"
    )
    schema_editor.execute("DROP FUNCTION IF EXISTS evaluations_reject_result_mutation();")


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0001_initial")]

    operations = [
        migrations.RunPython(
            create_append_only_trigger,
            reverse_code=drop_append_only_trigger,
        )
    ]
