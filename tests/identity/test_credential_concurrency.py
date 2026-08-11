import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from django.contrib.auth.models import User
from django.db import close_old_connections, connection, connections
from django.test import override_settings
from django.utils import timezone
from django_otp.oath import TOTP
from identity.exceptions import IdentityError
from identity.models import AdministratorProfile, RecoveryCode
from identity.services.credentials import (
    begin_totp_enrollment,
    consume_recovery_code,
    generate_recovery_batch,
    verify_totp,
)


def write_identity_key_file(path: Path) -> None:
    key = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
    path.write_text(
        json.dumps({"active_key_id": "identity-test", "keys": {"identity-test": key}}),
        encoding="utf-8",
    )


def run_concurrently(operation):
    barrier = Barrier(2)

    def invoke() -> bool:
        close_old_connections()
        barrier.wait()
        try:
            operation()
        except IdentityError:
            return False
        finally:
            connections.close_all()
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        return [future.result() for future in [executor.submit(invoke), executor.submit(invoke)]]


@pytest.mark.django_db(transaction=True)
def test_same_totp_counter_succeeds_at_most_once_under_concurrency(tmp_path: Path) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock concurrency is verified against PostgreSQL.")
    user = User.objects.create_user(username="synthetic.concurrent.totp")
    profile = AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ACTIVE,
    )
    key_path = tmp_path / "identity.json"
    write_identity_key_file(key_path)
    now = timezone.now().replace(microsecond=0)
    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=key_path):
        enrollment = begin_totp_enrollment(profile, label="Synthetic", now=now)
        enrollment.device.confirmed = True
        enrollment.device.save(update_fields=["confirmed"])
        secret = base64.b32decode(
            enrollment.manual_secret + "=" * (-len(enrollment.manual_secret) % 8)
        )
        generator = TOTP(secret, 30, 0, 6, 0)
        generator.time = now.timestamp()
        token = f"{generator.token():06d}"
        results = run_concurrently(
            lambda: verify_totp(enrollment.device, token, now=now)
        )

    assert sorted(results) == [False, True]


@pytest.mark.django_db(transaction=True)
def test_same_recovery_code_succeeds_at_most_once_under_concurrency() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock concurrency is verified against PostgreSQL.")
    user = User.objects.create_user(username="synthetic.concurrent.recovery")
    profile = AdministratorProfile.objects.create(
        user=user,
        status=AdministratorProfile.Status.ACTIVE,
    )
    now = timezone.now()
    presented = generate_recovery_batch(profile, now=now)[0]

    results = run_concurrently(
        lambda: consume_recovery_code(profile, presented, now=now)
    )

    assert sorted(results) == [False, True]
    assert RecoveryCode.objects.get(public_id=presented.split("-", 1)[0]).consumed_at == now
