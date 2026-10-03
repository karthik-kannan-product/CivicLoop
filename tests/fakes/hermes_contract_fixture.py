"""Container-side isolated synthetic setup and content-free inspection."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def identities():
    from deploy.hermes.identity_init import ASSERTION, CONSUMERS, GATEWAY

    source = Path("/source")
    source.mkdir(exist_ok=True)
    names = {name for _, allowed in CONSUMERS.values() for name in allowed}
    values = {name: secrets.token_urlsafe(48) for name in names}
    for name, value in values.items():
        target = source / name
        target.write_text(value)
        target.chmod(0o400)
    from deploy.hermes.identity_init import stage_identities

    stage_identities()
    handoff = Path("/gateway/current")
    handoff.mkdir(parents=True, exist_ok=True)
    gateway_values = {
        "provider-credential": "sk-fixture-" + secrets.token_urlsafe(32),
        "litellm-master-key": "sk-fixture-" + secrets.token_urlsafe(32),
        "gateway-token": values[GATEWAY],
        "budget-assertion-key": values[ASSERTION],
    }
    receipt = {
        "schema_version": 1,
        "component": "litellm",
        "environment": "production",
        "operations_sha": os.environ["CIVICLOOP_OPERATIONS_SHA"],
        "target_index_digest": os.environ["LITELLM_INDEX_DIGEST"],
        "target_platform_digest": os.environ["LITELLM_PLATFORM_DIGEST"],
        "expires_at": int(time.time()) + 1800,
        "approval_digest": "sha256:" + digest("synthetic"),
        "signature_status": "verified",
        "files": {
            name: "sha256:" + hashlib.sha256(value.encode()).hexdigest()
            for name, value in gateway_values.items()
        },
    }
    for name, value in {**gateway_values, "receipt.json": json.dumps(receipt)}.items():
        target = handoff / name
        target.write_text(value)
        target.chmod(0o400)
        os.chown(target, 65534, 65534)
    handoff.chmod(0o500)
    os.chown(handoff, 65534, 65534)
    ledger = Path("/ledger")
    ledger.chmod(0o700)
    os.chown(ledger, 65534, 65534)
    identity = Path("/owner/identity.json")
    identity.write_text(
        json.dumps({"active_key_id": "fixture", "keys": {"fixture": secrets.token_urlsafe(32)}})
    )
    identity.chmod(0o400)
    os.chown(identity, 10001, 10001)
    phoenix_admin = "synthetic9" + secrets.token_hex(32)
    phoenix = {
        "PHOENIX_SECRET": "synthetic7" + secrets.token_hex(32),
        "PHOENIX_ADMIN_SECRET": phoenix_admin,
        "PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD": "Synthetic9!" + secrets.token_hex(24),
    }
    phoenix_files = (
        (Path("/phoenix-identity/auth.json"), json.dumps(phoenix), 65532),
        (Path("/telemetry-identity/otlp-token"), phoenix_admin, 10001),
        (Path("/telemetry-identity/otlp-headers"), "authorization=Bearer " + phoenix_admin, 10001),
    )
    for target, content, uid in phoenix_files:
        target.write_text(content)
        target.chmod(0o400)
        os.chown(target, uid, uid)
    # Root has only CHOWN, with no DAC_OVERRIDE or FOWNER. Complete every write
    # before sealing parents, and chmod each root-owned directory before chown.
    for directory, uid in (
        (Path("/phoenix-identity"), 65532),
        (Path("/telemetry-identity"), 10001),
    ):
        directory.chmod(0o700)
        os.chown(directory, uid, uid)
    Path("/phoenix-data").chmod(0o700)
    os.chown("/phoenix-data", 65532, 65532)
    return {"status": "ready", "consumer_count": len(CONSUMERS)}


def phoenix_start():
    values = json.loads(Path("/phoenix-identity/auth.json").read_text())
    if set(values) != {
        "PHOENIX_SECRET",
        "PHOENIX_ADMIN_SECRET",
        "PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD",
    }:
        raise ValueError
    os.environ.update(values)
    os.execv(sys.executable, [sys.executable, "-m", "phoenix.server.main", "serve"])


def django_setup():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "civicloop.settings")
    import django

    django.setup()


def seed(*, draft=False):
    django_setup()
    from agents.models import ModelProfile, RoutingPolicy
    from django.contrib.auth.models import User
    from django.test import Client
    from django.utils import timezone
    from identity.models import AdministratorProfile, AdministratorSession
    from identity.services.sessions import ADMIN_SESSION_KEY
    from launchloop.engine import prepare_package
    from launchloop.models import DemoActor, Event, EventRevision, Workflow
    from launchloop.services import NEW_YORK_EVENT, package_hash

    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username="fixture-owner-" + suffix)
    user.set_unusable_password()
    user.save()
    profile = AdministratorProfile.objects.create(user=user, status="active")
    actor = DemoActor.objects.create(
        slug="fixture-owner-" + suffix, display_name="Synthetic owner", role="operator", user=user
    )
    event = Event.objects.create(slug="fixture-event-" + suffix, title="Synthetic event")
    snapshot = dict(
        NEW_YORK_EVENT,
        venue_name="Synthetic venue",
        venue_address="1 Test St",
        access_instructions="Synthetic entry",
    )
    revision = EventRevision.objects.create(event=event, version=1, snapshot=snapshot, author=actor)
    package = prepare_package(snapshot)
    workflow = Workflow.objects.create(
        event=event,
        revision=revision,
        package=package,
        package_hash=package_hash(package),
        status="draft" if draft else "ready_for_review",
    )
    if not ModelProfile.objects.filter(profile_id="task8_fixture", revision=1).exists():
        model = ModelProfile.objects.create(
            profile_id="task8_fixture",
            revision=1,
            provider="openai",
            model="gpt-5-mini",
            purpose="workflow",
            max_input_tokens=500000,
            max_output_tokens=100000,
            temperature="0.20",
            input_price_microusd_per_million=400000,
            output_price_microusd_per_million=1600000,
        )
        RoutingPolicy.objects.create(
            policy_id="task8_fixture_policy",
            revision=1,
            purpose="workflow",
            model_profile=model,
            per_run_limit_microusd=500000,
            monthly_limit_microusd=25000000,
        )
    client = Client()
    client.force_login(user)
    session = client.session
    now = timezone.now()
    metadata = AdministratorSession.objects.create(
        profile=profile,
        session_key=session.session_key,
        authenticated_at=now,
        last_activity_at=now,
        mfa_verified_at=now,
        fresh_verified_at=now,
        absolute_expires_at=now + timedelta(hours=12),
        expires_at=now + timedelta(minutes=30),
        device_label="Synthetic fixture",
        source_ip="192.0.2.44",
    )
    session[ADMIN_SESSION_KEY] = str(metadata.id)
    session.save()
    csrf = secrets.token_hex(16)
    # Fixture credentials are transferred through stdin/stdout capture only; the
    # orchestrator never prints this internal payload or includes it in evidence.
    return {
        "workflow_id": str(workflow.id),
        "revision_id": revision.id,
        "session": session.session_key,
        "csrf": csrf,
        "package_digest": workflow.package_hash,
    }


def inspect(run_id):
    django_setup()
    from agents.models import (
        AgentRun,
        BudgetReservation,
        DraftOperation,
        MCPSubmission,
        WorkflowCapability,
    )
    from launchloop.models import ConnectorExecution

    run = AgentRun.objects.get(pk=run_id)
    correlation = run.hermes_binding.correlation_id
    proposals = MCPSubmission.objects.filter(
        capability__correlation_id=correlation, kind="proposal"
    )
    operations = DraftOperation.objects.filter(proposal__capability__correlation_id=correlation)
    reservation = BudgetReservation.objects.get(run_id=run.id)
    capability = WorkflowCapability.objects.filter(correlation_id=correlation).first()
    events = list(run.events.order_by("sequence").values("event_type", "outcome", "detail_digest"))
    return {
        "terminal_status": run.status,
        "proposal_count": proposals.count(),
        "pending_operation_count": operations.filter(status="pending").count(),
        "pending_provider_count": operations.values("provider").distinct().count(),
        "operation_count": operations.count(),
        "approval_count": operations.exclude(approval=None).count(),
        "receipt_count": operations.exclude(receipt=None).count(),
        "provider_call_count": ConnectorExecution.objects.count(),
        "capability_revoked": capability is None or capability.revoked_at is not None,
        "reservation_status": reservation.status,
        "package_digest": run.workflow.package_hash,
        "events_digest": digest(events),
        "event_count": len(events),
        "trace_id": run.trace_id,
        "correlation_digest": digest(str(correlation)),
    }


def nonces():
    with sqlite3.connect("/ledger/ledger.sqlite3") as database:
        rows = database.execute("SELECT nonce FROM nonces ORDER BY nonce").fetchall()
    return {
        "nonce_count": len(rows),
        "distinct_nonce_count": len(set(rows)),
        "nonce_digest": digest(rows),
    }


if __name__ == "__main__":
    try:
        command = sys.argv[1]
        result = {
            "identities": identities,
            "seed": seed,
            "seed-draft": lambda: seed(draft=True),
            "phoenix": phoenix_start,
            "nonces": nonces,
        }.get(command)
        payload = result() if result else inspect(sys.argv[2]) if command == "inspect" else None
        if payload is None:
            raise ValueError
        print(json.dumps(payload, separators=(",", ":")))
    except Exception:
        print('{"status":"fixture_unavailable"}')
        raise SystemExit(1) from None
