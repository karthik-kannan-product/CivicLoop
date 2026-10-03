"""Synthetic Linux permission proof; never reads host identity files."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from deploy.hermes.identity_init import CONSUMERS, stage_identities


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        root.chmod(0o711)
        source = root / "source"
        source.mkdir(mode=0o700)
        handoff = root / "handoff"
        handoff.mkdir(mode=0o711)
        names = {name for _, allowed in CONSUMERS.values() for name in allowed}
        for name in names:
            path = source / name
            path.write_bytes(("synthetic-only-" + name * 2).encode())
            path.chmod(0o400)
        for consumer in CONSUMERS:
            (handoff / consumer).mkdir()
        stage_identities(source=source, handoff=handoff)
        stage_identities(source=source, handoff=handoff)  # Rotation also preserves ownership.
        try:
            unsafe = source / next(iter(names))
            unsafe.chmod(0o644)
            try:
                stage_identities(source=source, handoff=handoff)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Unsafe source permissions accepted")
            unsafe.chmod(0o400)
            for consumer, (uid, allowed) in CONSUMERS.items():
                code = (
                    "import os,stat; from pathlib import Path; "
                    f"os.setuid({uid}); p=Path({str(handoff / consumer)!r}); "
                    f"assert set(x.name for x in p.iterdir()) == set({allowed!r}); "
                    f"assert p.stat().st_uid == {uid}; "
                    "assert stat.S_IMODE(p.stat().st_mode)==0o700; "
                    f"assert all(x.stat().st_uid=={uid} and stat.S_IMODE(x.stat().st_mode)==0o400 "
                    "and len(x.read_bytes())>=16 for x in p.iterdir())"
                )
                result = subprocess.run(
                    [sys.executable, "-c", code], capture_output=True, timeout=5
                )
                assert result.returncode == 0, "Consumer permission proof failed"
            other = handoff / "transport"
            code = (
                "import os; from pathlib import Path; os.setuid(10000); "
                f"p=Path({str(other)!r});\n"
                "try: list(p.iterdir())\n"
                "except PermissionError: pass\n"
                "else: raise AssertionError('Cross-consumer access allowed')"
            )
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=5)
            assert result.returncode == 0, "Isolation proof failed"
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "consumer_count": 5,
                        "rotation": True,
                        "cross_consumer_denied": True,
                    }
                )
            )
        finally:
            for consumer in CONSUMERS:
                os.chown(handoff / consumer, 0, 0)


if __name__ == "__main__":
    main()
