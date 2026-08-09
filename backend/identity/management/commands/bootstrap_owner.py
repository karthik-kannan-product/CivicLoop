import getpass

from django.core.management.base import BaseCommand, CommandError

from identity.exceptions import IdentityError
from identity.models import AdministratorProfile
from identity.services.credentials import bootstrap_owner


class Command(BaseCommand):
    help = "Interactively create the single CivicLoop owner account."

    def handle(self, *args, **options) -> None:
        if AdministratorProfile.objects.exclude(
            status=AdministratorProfile.Status.DISABLED
        ).exists():
            raise CommandError("An enabled owner already exists.")

        username = input("Owner username: ").strip()
        email = input("Owner email: ").strip()
        password = getpass.getpass("Owner password: ")
        confirmation = getpass.getpass("Confirm owner password: ")
        if password != confirmation:
            raise CommandError("Password confirmation does not match.")
        try:
            profile = bootstrap_owner(username=username, email=email, password=password)
        except IdentityError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            self.style.SUCCESS(
                f"Created CivicLoop owner {profile.user.username}; TOTP enrollment is required."
            )
        )
