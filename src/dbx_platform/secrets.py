"""Unified secret access across Databricks and Azure constructs.

One function, two reference schemes:

- ``dbx://<scope>/<key>``      — Databricks secret scope (including Azure
  Key Vault-backed scopes). Uses the REST API, so it works both locally and
  inside jobs (unlike dbutils.secrets, which is runtime-only).
- ``akv://<vault-name>/<name>`` — direct Azure Key Vault access.

Azure credential resolution (best-practice ordered):

1. Unity Catalog service credential — managed identity via access connector,
   no secrets to rotate. Used automatically when running inside a Databricks
   runtime and a credential name is configured (DBX_PLATFORM_SERVICE_CREDENTIAL
   or the ``service_credential`` argument). See docs/secrets.md for setup.
2. DefaultAzureCredential — covers managed identity on Azure hosts and
   ``az login`` for local development.

Direct AKV access needs the optional extra: ``pip install dbx-platform[azure]``.
"""

from __future__ import annotations

import base64
import os
from typing import NamedTuple

from dbx_platform.client import get_client
from dbx_platform.config import Settings


class SecretRef(NamedTuple):
    scheme: str  # "dbx" | "akv"
    container: str  # secret scope or vault name
    key: str  # secret key or secret name


def parse_secret_ref(ref: str) -> SecretRef:
    """Parse 'dbx://scope/key' or 'akv://vault/name'."""
    for scheme in ("dbx", "akv"):
        prefix = f"{scheme}://"
        if ref.startswith(prefix):
            rest = ref[len(prefix):]
            container, sep, key = rest.partition("/")
            if not container or not sep or not key:
                raise ValueError(
                    f"Invalid secret ref '{ref}': expected {scheme}://<container>/<key>"
                )
            return SecretRef(scheme, container, key)
    raise ValueError(
        f"Invalid secret ref '{ref}': must start with dbx:// (Databricks secret scope) "
        "or akv:// (Azure Key Vault)"
    )


def _in_databricks_runtime() -> bool:
    return "DATABRICKS_RUNTIME_VERSION" in os.environ


def get_credential(service_credential: str | None = None):
    """Return an azure-identity-compatible credential.

    Prefers a Unity Catalog service credential when running in a Databricks
    runtime; falls back to DefaultAzureCredential (managed identity / az login).
    """
    name = service_credential or Settings.from_env().service_credential
    if name and _in_databricks_runtime():
        from databricks.sdk.runtime import dbutils  # only importable in-runtime

        return dbutils.credentials.getServiceCredentialsProvider(name)
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as e:
        raise ImportError(
            "Azure libraries not installed. Run: pip install 'dbx-platform[azure]'"
        ) from e
    return DefaultAzureCredential()


def get_secret(
    ref: str,
    *,
    profile: str | None = None,
    credential=None,
    service_credential: str | None = None,
) -> str:
    """Fetch a secret value by reference. See module docstring for schemes."""
    parsed = parse_secret_ref(ref)
    if parsed.scheme == "dbx":
        w = get_client(profile)
        resp = w.secrets.get_secret(scope=parsed.container, key=parsed.key)
        return base64.b64decode(resp.value).decode("utf-8")

    try:
        from azure.keyvault.secrets import SecretClient
    except ImportError as e:
        raise ImportError(
            "Azure libraries not installed. Run: pip install 'dbx-platform[azure]'"
        ) from e
    cred = credential or get_credential(service_credential)
    client = SecretClient(
        vault_url=f"https://{parsed.container}.vault.azure.net", credential=cred
    )
    return client.get_secret(parsed.key).value
