#!/usr/bin/env python3
"""Encrypt the private top items so they can sit in this public repo.

Usage: BRIEF_KEY=<key> python3 -I scripts/brief_seal.py <items.json> private.enc.json

items.json is {"items": [{"title": "...", "href": "https://..."}]}. The key lives outside
this repo; only devices that opened the key link can read the output. AES-256-CBC with
HMAC-SHA256 over iv + ciphertext. Needs the openssl command, nothing else.
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
from datetime import datetime, timezone


def main(src, dst):
    key = os.environ["BRIEF_KEY"].strip()
    raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    if len(raw) != 32:
        sys.exit("BRIEF_KEY must be 32 bytes")
    enc, mac = hashlib.sha256(raw + b"enc").digest(), hashlib.sha256(raw + b"mac").digest()
    data = json.load(open(src))
    body = {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "items": [{"title": str(i.get("title", ""))[:110], "href": str(i.get("href", ""))[:300]}
                      for i in data.get("items", [])][:3]}
    iv = os.urandom(16)
    ct = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-K", enc.hex(), "-iv", iv.hex()],
                        input=json.dumps(body, ensure_ascii=False).encode(), capture_output=True, check=True).stdout
    tag = hmac.new(mac, iv + ct, hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()
    json.dump({"v": 1, "iv": b64(iv), "ct": b64(ct), "mac": b64(tag)}, open(dst, "w"))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
