"""Hash-guarded, one-file patch for the reviewed Hermes auxiliary HTTP lane."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

SOURCE_SHA256 = "becbb3f3a6b9b847b95791e0c43d2689d5f07726f75b106f13afad8d45c5c9c7"

HELPER = """

def _civicloop_nonstream_kwargs(client, kwargs):
    from hermes_cli.config import load_config_readonly
    from urllib.parse import urlsplit
    config = load_config_readonly() or {}
    if config.get("auxiliary", {}).get("civicloop_nonstreaming") is not True:
        return None
    url = urlsplit(str(getattr(client, "base_url", "")))
    if (url.scheme != "http" or url.hostname != "127.0.0.1"
            or url.path.rstrip("/") != "/v1" or url.username or url.password
            or url.query or url.fragment or not url.port
            or kwargs.get("model") != "civicloop-default"):
        raise RuntimeError("CivicLoop auxiliary route unavailable")
    bounded = dict(kwargs)
    bounded.pop("stream_options", None)
    bounded["stream"] = False
    return bounded
"""


def render_replacement(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("source drift: agent/auxiliary_client.py")
    text = source.decode("utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    targets = {"_create_with_progress_once": False, "_acreate_with_progress": True}
    inserts = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
            doc = node.body[0]
            if not isinstance(doc, ast.Expr) or not isinstance(doc.value, ast.Constant):
                raise ValueError("auxiliary function structure drift")
            await_prefix = "await " if targets[node.name] else ""
            code = (
                "    bounded = _civicloop_nonstream_kwargs(client, kwargs)\n"
                "    if bounded is not None:\n"
                "        _notify_aux_dispatch()\n"
                "        _notify_aux_progress()\n"
                f"        response = {await_prefix}client.chat.completions.create(**bounded)\n"
                "        _notify_aux_provider_response()\n"
                "        return response\n"
            )
            inserts.append((doc.end_lineno, code))
    if len(inserts) != 2:
        raise ValueError("auxiliary function structure drift")
    for offset, code in sorted(inserts, reverse=True):
        lines.insert(offset, code)
    result = ("".join(lines) + HELPER).encode("utf-8")
    ast.parse(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--print-hash", action="store_true")
    args = parser.parse_args()
    target = args.root / "agent/auxiliary_client.py"
    replacement = render_replacement(target.read_bytes())
    digest = hashlib.sha256(replacement).hexdigest()
    if args.print_hash:
        print(digest)
        return
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
    entry = manifest["files"][0]
    if entry["path"] != "agent/auxiliary_client.py" or digest != entry["replacement_sha256"]:
        raise SystemExit("replacement drift: agent/auxiliary_client.py")
    target.write_bytes(replacement)


if __name__ == "__main__":
    main()
