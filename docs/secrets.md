# Secrets: Key Vault, managed identities, and Databricks constructs

`dbx_platform.secrets` gives you one function that works across Databricks and
Azure constructs, locally and inside jobs/notebooks:

```python
from dbx_platform.secrets import get_secret

get_secret("dbx://my-scope/api-key")          # Databricks secret scope (incl. AKV-backed)
get_secret("akv://corp-vault/db-password")    # Azure Key Vault directly
```

Install the Azure extra for `akv://` refs: `pip install -e ".[azure]"` (or
`%pip install /Volumes/.../dbx_platform-...whl azure-identity azure-keyvault-secrets`
in a notebook).

## How Azure auth is resolved

`get_credential()` picks the first that applies:

1. **Unity Catalog service credential** — when running inside a Databricks
   runtime and a credential name is set (env `DBX_PLATFORM_SERVICE_CREDENTIAL`
   or the `service_credential=` argument). This is the modern recommended
   pattern: a managed identity, governed by Unity Catalog, no secret rotation.
2. **DefaultAzureCredential** — managed identity on Azure hosts, or your
   `az login` session for local development.

You can also pass any azure-identity credential explicitly:
`get_secret("akv://v/n", credential=my_credential)`.

## Setting up a UC service credential for Key Vault (one-time)

1. **Access connector with a user-assigned managed identity (UAMI)**
   - Create a UAMI (`az identity create ...`).
   - Create an *Azure Databricks Access Connector* and attach the UAMI.
   - Prefer a dedicated connector for Key Vault access — it keeps RBAC granular,
     and a user-assigned (not system) identity survives resource-group moves.
2. **Grant the identity access to the vault**
   - Key Vault → Access control (IAM) → add role **Key Vault Secrets User**
     to the UAMI (RBAC permission model).
3. **Create the service credential in Databricks**
   - Catalog → External Data → Credentials → **Create credential** →
     type *Service credential*, paste the access connector's resource ID (and
     the UAMI's resource ID).
   - Grant `ACCESS` on the credential to the principals that need it.
4. **Use it**
   ```bash
   export DBX_PLATFORM_SERVICE_CREDENTIAL=<credential-name>
   ```
   ```python
   get_secret("akv://corp-vault/db-password")   # in a notebook/job: uses the MI
   ```

## When to use which construct

| Construct | Use when | Notes |
|---|---|---|
| **UC service credential** (`akv://` in-workspace) | New work; anything governed by UC | MI-based, no rotation, workspace-agnostic, UC-audited |
| **AKV-backed secret scope** (`dbx://`) | Existing scopes; secrets shared with non-UC workloads | Read-only from Databricks; scope creation needs the AKV *Vault access policy* model or careful RBAC setup |
| **Direct AKV + DefaultAzureCredential** (`akv://` locally) | Local dev, CI on Azure | `az login` locally; managed identity on Azure compute |

`dbx://` refs use the workspace REST API (`w.secrets.get_secret`), so unlike
`dbutils.secrets` they also work from your laptop — handy for testing the same
code path you'll run in a job. Values fetched this way are plain strings in
your process: never print or log them.

## Distributing this package to notebooks via a UC Volume

```bash
python -m build --wheel
dbx-platform release publish-wheel --volume /Volumes/main/dbx_platform/wheels
```

Then from any notebook:

```python
%pip install /Volumes/main/dbx_platform/wheels/dbx_platform-0.1.0-py3-none-any.whl
from dbx_platform.secrets import get_secret
```

(Create the volume once: `CREATE VOLUME dbx_dev.dbx_platform.wheels;` — and note
that bundle-deployed jobs do *not* need this; the bundle ships its own wheel.)
