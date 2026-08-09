from django.core.management.base import BaseCommand, CommandError

from identity.exceptions import IdentityError
from identity.models import AdministratorProfile
from identity.services.credentials import reset_owner_mfa


class Command(BaseCommand):
    help = "Revoke all owner sessions and require new TOTP enrollment."

    def handle(self, *args, **options) -> None:
        try:
            profile = AdministratorProfile.objects.select_related("user").exclude(
                status=AdministratorProfile.Status.DISABLED
            ).get()
        except AdministratorProfile.DoesNotExist:
            raise CommandError("No enabled owner exists.") from None
        except AdministratorProfile.MultipleObjectsReturned:
            raise CommandError("Owner state is inconsistent; no reset was performed.") from None

        self.stdout.write(
            "This revokes every administrator session, disables every TOTP device, "
            "invalidates every recovery code, and requires enrollment again."
        )
        confirmed_username = input(
            f"Type the owner username '{profile.user.username}' to continue: "
        ).strip()
        try:
            reset_owner_mfa(
                profile=profile,
                confirmed_username=confirmed_username,
            )
        except IdentityError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            self.style.SUCCESS(
                f"Reset MFA for {profile.user.username}; all administrator sessions were revoked."
            )
        )
