from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import binascii
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = Path(__file__).resolve().parent / "private" / "rag_hidden_query_map.json.enc"


def _keystream(key: bytes, nonce: bytes, size: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < size:
        output.extend(
            hmac.new(
                key,
                nonce + counter.to_bytes(8, "big"),
                hashlib.sha256,
            ).digest()
        )
        counter += 1
    return bytes(output[:size])


def _outside_repo(path: Path) -> None:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    raise SystemExit("Decrypted blind query map must be written outside the repository.")


def _decode_b64(payload: dict[str, object], field: str) -> bytes:
    value = payload.get(field)
    if not isinstance(value, str):
        raise SystemExit(f"Encrypted holdout payload is missing string field: {field}")
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as exc:
        raise SystemExit(f"Encrypted holdout payload has invalid base64 in {field}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Decrypt the private blind query map outside the repository.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    password = os.environ.get("RAG_HOLDOUT_KEY", "")
    if not password:
        raise SystemExit("Set RAG_HOLDOUT_KEY before running this command.")

    output = Path(args.output).expanduser().resolve()
    _outside_repo(output)

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if payload.get("algorithm") != "scrypt+hmac-sha256-stream-v1":
        raise SystemExit("Unsupported encrypted holdout format.")

    salt = _decode_b64(payload, "salt_b64")
    nonce = _decode_b64(payload, "nonce_b64")
    ciphertext = _decode_b64(payload, "ciphertext_b64")
    expected_mac = _decode_b64(payload, "mac_b64")

    master = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    enc_key, mac_key = master[:32], master[32:]
    actual_mac = hmac.new(
        mac_key,
        b"rag-holdout-v1" + salt + nonce + ciphertext,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected_mac, actual_mac):
        raise SystemExit("Invalid RAG_HOLDOUT_KEY or corrupted encrypted query map.")

    stream = _keystream(enc_key, nonce, len(ciphertext))
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream, strict=True))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(plaintext)
    print(f"Decrypted blind query map: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
