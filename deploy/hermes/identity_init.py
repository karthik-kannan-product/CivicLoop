"""Stage approved identities into isolated, read-only consumer handoffs."""

import os
import stat
import tempfile
from pathlib import Path

SERVICE = "civicloop-hermes-service-token"
UPSTREAM = "civicloop-hermes-upstream-token"
CONTROLLER = "civicloop-hermes-controller-token"
CLIENT = "civicloop-hermes-shim-client-token"
CONTROL = "civicloop-hermes-transport-control-token"
MCP = "civicloop-mcp-token"
GATEWAY = "civicloop-hermes-gateway-token"
ASSERTION = "civicloop-hermes-budget-assertion-key"
CONSUMERS = {
    "worker": (10001, (SERVICE,)),
    "adapter": (10001, (SERVICE, UPSTREAM, CONTROLLER, CONTROL)),
    "controller": (10000, (CONTROLLER, CLIENT)),
    "transport": (10001, (CONTROL, CLIENT, MCP, GATEWAY, ASSERTION)),
    "mcp": (10001, (MCP,)),
}


def _read_identity(path, *, assertion=False):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) not in (0o400, 0o600)
            or metadata.st_nlink != 1
        ):
            raise ValueError
        value = handle.read(4097).strip()
    if assertion:
        if not 32 <= len(value) <= 4096:
            raise ValueError
    elif not 16 <= len(value) <= 512 or not all(33 <= char <= 126 for char in value):
        raise ValueError
    return value


def stage_identities(*, source=Path("/source"), handoff=Path("/handoff")):
    try:
        names = {name for _, allowed in CONSUMERS.values() for name in allowed}
        values = {
            name: _read_identity(source / name, assertion=name == ASSERTION) for name in names
        }
        if len(set(values.values())) != len(values):
            raise ValueError
        for consumer, (uid, allowed) in CONSUMERS.items():
            destination = handoff / consumer
            metadata = destination.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in (0, uid)
                or metadata.st_gid not in (0, uid)
            ):
                raise ValueError
            # Compose file-backed secrets cannot remap ownership. This offline
            # root-only initializer owns the writable handoff during rotation.
            os.chown(destination, 0, 0)
            destination.chmod(0o700)
            if any(item.name not in allowed for item in destination.iterdir()):
                raise ValueError
            for name in allowed:
                temporary = None
                try:
                    descriptor, temporary = tempfile.mkstemp(prefix=".identity-", dir=destination)
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(values[name])
                        handle.flush()
                        os.fsync(handle.fileno())
                        os.fchmod(handle.fileno(), 0o400)
                        os.fchown(handle.fileno(), uid, uid)
                    os.replace(temporary, destination / name)
                finally:
                    if temporary is not None:
                        Path(temporary).unlink(missing_ok=True)
            os.chown(destination, uid, uid)
        return {"status": "ready", "consumer_count": len(CONSUMERS)}
    except Exception:
        raise RuntimeError("Identity staging unavailable") from None


if __name__ == "__main__":
    stage_identities()
