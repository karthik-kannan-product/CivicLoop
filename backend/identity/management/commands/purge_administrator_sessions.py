from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from identity.models import AdministratorSession


class Command(BaseCommand):
    help = "Revoke expired or orphaned administrator sessions and purge expired Django sessions."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        now = timezone.now()
        Session.objects.filter(expire_date__lte=now).delete()
        active = AdministratorSession.objects.select_for_update().filter(revoked_at__isnull=True)
        expired = active.filter(
            Q(expires_at__isnull=True)
            | Q(expires_at__lte=now)
            | Q(absolute_expires_at__isnull=True)
            | Q(absolute_expires_at__lte=now)
        )
        expired_keys = list(expired.values_list("session_key", flat=True))
        expired_count = expired.update(revoked_at=now)
        Session.objects.filter(session_key__in=expired_keys).delete()

        live_keys = set(Session.objects.values_list("session_key", flat=True))
        orphaned = AdministratorSession.objects.select_for_update().filter(
            revoked_at__isnull=True
        ).exclude(session_key__in=live_keys)
        orphaned_count = orphaned.update(revoked_at=now)
        self.stdout.write(
            self.style.SUCCESS(
                f"Revoked {expired_count + orphaned_count} administrator session(s)."
            )
        )
