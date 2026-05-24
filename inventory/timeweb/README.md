# Timeweb Inventory

`inventory.public.json` is a sanitized snapshot from Timeweb API.

It is safe to commit because sensitive values are replaced with `secret_refs`.

Local sensitive values discovered from the API are stored outside Git:

```text
.secrets/timeweb-import-secrets.json
```

Do not commit `.secrets/`.

## Current Scope

The current snapshot covers all Timeweb projects visible to the token:

- projects
- cloud servers
- server disks
- managed databases
- S3 buckets
- SSH public keys
- floating IPs
- project-level domain/mailbox resource summaries

Empty resource groups are also recorded so that future discovery can notice when new platform areas appear.

## Secret Placement

For CI, move values from `.secrets/timeweb-import-secrets.json` into either:

- GitHub Actions secrets for bootstrapping OpenTofu state and first imports.
- OpenBao or Infisical after the platform secrets stack exists.

OpenTofu state backend credentials should be GitHub Actions secrets:

```text
TOFU_STATE_ACCESS_KEY
TOFU_STATE_SECRET_KEY
```

Timeweb provider access should stay in:

```text
TIMEWEB_TOKEN
```
