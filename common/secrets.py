"""Secret storage: the OS keyring (Windows Credential Manager, macOS Keychain,
Secret Service) or, in containers, an encrypted file inside DATA_DIR.

Two separate stores, each with one writer: account passwords (the executor; they never
leave it) and LLM provider keys (main writes them, the services that call the LLM read
them)."""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import keyring
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from keyring.errors import KeyringError

from common.errors import ApiError
from common.settings import Settings


log = logging.getLogger("secrets")

class SecretStore:
    def __init__(self, backend: str, service: str, file_path: Path, password: str):

        self.backend   = backend
        # The keyring's service name; with the file backend, its own file.
        self.service   = service
        self.file_path = file_path
        self.password  = password


    @classmethod
    def accounts(cls, settings: Settings) -> SecretStore:
        """Account passwords: written and read by the executor only."""
        return cls._of(settings, "fintech2026", "secrets.enc")


    @classmethod
    def llm_keys(cls, settings: Settings) -> SecretStore:
        """LLM provider keys: written by main, read wherever the LLM is called."""
        return cls._of(settings, "fintech2026-llm", "llm_keys.enc")


    @classmethod
    def _of(cls, settings: Settings, service: str, file_name: str) -> SecretStore:
        return cls(
            backend=settings.secrets.keyring_backend,
            service=service,
            file_path=settings.storage.data_dir / file_name,
            password=settings.secrets.keyring_password,
        )


    def set(self, key: str, value: str) -> None:
        if self.backend == "system":
            try:
                keyring.set_password(self.service, key, value)
                return

            except KeyringError as e:
                raise ApiError(
                    status_code=503,
                    message="System keyring is unavailable; set KEYRING_BACKEND=file and KEYRING_PASSWORD",
                    details={"error": repr(e)},
                ) from e

        data = self._load_file()
        data[key] = value
        self._save_file(data)


    def get(self, key: str) -> str | None:
        if self.backend == "system":
            try:
                return keyring.get_password(self.service, key)

            except KeyringError as e:
                raise ApiError(status_code=503, message="System keyring is unavailable", details={"error": repr(e)}) from e

        return self._load_file().get(key)


    def delete(self, key: str) -> None:
        if self.backend == "system":
            try:
                keyring.delete_password(self.service, key)

            except KeyringError:
                pass

            return

        data = self._load_file()
        if data.pop(key, None) is not None:
            self._save_file(data)


    def _fernet(self, salt: bytes) -> Fernet:
        if not self.password:
            raise ApiError(status_code=503, message="KEYRING_PASSWORD is required for KEYRING_BACKEND=file")

        kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
        return Fernet(base64.urlsafe_b64encode(kdf.derive(self.password.encode())))


    def _load_file(self) -> dict[str, str]:
        if not self.file_path.is_file():
            return {}

        envelope = json.loads(self.file_path.read_text(encoding="utf-8"))
        salt = base64.b64decode(envelope["salt"])

        try:
            plain = self._fernet(salt).decrypt(envelope["data"].encode())

        except InvalidToken as e:
            raise ApiError(status_code=503, message="Cannot decrypt secrets file: wrong KEYRING_PASSWORD") from e

        return json.loads(plain)


    def _save_file(self, data: dict[str, str]) -> None:
        salt = os.urandom(16)
        token = self._fernet(salt).encrypt(json.dumps(data).encode()).decode()

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"salt": base64.b64encode(salt).decode(), "data": token}), encoding="utf-8")
        tmp.replace(self.file_path)
