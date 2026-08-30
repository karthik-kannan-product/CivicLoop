from django.db import migrations

FUNCTIONS_SQL = """
CREATE OR REPLACE FUNCTION agents_reject_immutable_row_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Versioned profile, policy, and ledger records are immutable.'
        USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION agents_guard_run_binding()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Durable agent runs cannot be deleted.' USING ERRCODE = '55000';
    END IF;
    IF OLD.workflow_id IS DISTINCT FROM NEW.workflow_id OR
       OLD.event_revision_id IS DISTINCT FROM NEW.event_revision_id OR
       OLD.package_hash IS DISTINCT FROM NEW.package_hash OR
       OLD.routing_policy_id IS DISTINCT FROM NEW.routing_policy_id OR
       OLD.model_profile_id IS DISTINCT FROM NEW.model_profile_id OR
       OLD.fixture_manifest_id IS DISTINCT FROM NEW.fixture_manifest_id OR
       OLD.fixture_manifest_revision IS DISTINCT FROM NEW.fixture_manifest_revision OR
       OLD.fixture_manifest_digest IS DISTINCT FROM NEW.fixture_manifest_digest OR
       OLD.privacy_mode IS DISTINCT FROM NEW.privacy_mode THEN
        RAISE EXCEPTION 'Agent run bindings are immutable.' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION agents_guard_step_binding()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Durable agent steps cannot be deleted.' USING ERRCODE = '55000';
    END IF;
    IF OLD.run_id IS DISTINCT FROM NEW.run_id OR
       OLD.sequence IS DISTINCT FROM NEW.sequence THEN
        RAISE EXCEPTION 'Agent step bindings are immutable.' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION agents_guard_reservation_binding()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Budget reservations cannot be deleted.' USING ERRCODE = '55000';
    END IF;
    IF OLD.run_id IS DISTINCT FROM NEW.run_id OR
       OLD.model_profile_id IS DISTINCT FROM NEW.model_profile_id OR
       OLD.routing_policy_id IS DISTINCT FROM NEW.routing_policy_id OR
       OLD.period_id IS DISTINCT FROM NEW.period_id OR
       OLD.estimated_input_tokens IS DISTINCT FROM NEW.estimated_input_tokens OR
       OLD.estimated_output_tokens IS DISTINCT FROM NEW.estimated_output_tokens OR
       OLD.reserved_cost_microusd IS DISTINCT FROM NEW.reserved_cost_microusd THEN
        RAISE EXCEPTION 'Budget reservation bindings are immutable.' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
"""

IMMUTABLE_TRIGGERS = {
    "agents_model_profile_immutable": "agents_modelprofile",
    "agents_routing_policy_immutable": "agents_routingpolicy",
    "agents_budget_ledger_immutable": "agents_budgetledgerrecord",
}

BINDING_TRIGGERS = {
    "agents_run_binding_guard": ("agents_agentrun", "agents_guard_run_binding"),
    "agents_step_binding_guard": ("agents_agentstep", "agents_guard_step_binding"),
    "agents_reservation_binding_guard": (
        "agents_budgetreservation",
        "agents_guard_reservation_binding",
    ),
}


def create_invariant_triggers(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(FUNCTIONS_SQL)
    for trigger, table in IMMUTABLE_TRIGGERS.items():
        schema_editor.execute(
            f"""CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION agents_reject_immutable_row_mutation();"""
        )
    for trigger, (table, function) in BINDING_TRIGGERS.items():
        schema_editor.execute(
            f"""CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {function}();"""
        )


def drop_invariant_triggers(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    for trigger, table in IMMUTABLE_TRIGGERS.items():
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table};")
    for trigger, (table, _function) in BINDING_TRIGGERS.items():
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table};")
    for function in [
        "agents_reject_immutable_row_mutation",
        "agents_guard_run_binding",
        "agents_guard_step_binding",
        "agents_guard_reservation_binding",
    ]:
        schema_editor.execute(f"DROP FUNCTION IF EXISTS {function}();")


class Migration(migrations.Migration):
    dependencies = [("agents", "0001_initial")]

    operations = [
        migrations.RunPython(
            create_invariant_triggers,
            reverse_code=drop_invariant_triggers,
        )
    ]
