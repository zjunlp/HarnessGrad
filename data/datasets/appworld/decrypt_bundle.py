#!/usr/bin/env python3
"""Decrypt an AppWorld bundle on stdin; run only in the prepared dependency runtime."""
import sys
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def main():
    data = sys.stdin.buffer.read()
    if len(data) < 16:
        raise ValueError('truncated AppWorld bundle')
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=b'Nvx#rYcYQ2%btf', iterations=100000).derive(b'WEquKLy##9M@qu')
    decryptor = Cipher(algorithms.AES(key), modes.CFB(data[:16])).decryptor()
    sys.stdout.buffer.write(decryptor.update(data[16:]) + decryptor.finalize())


if __name__ == '__main__':
    main()
