# OpenTofu State

OpenTofu state should not live only on a GitHub Actions runner.

## Local state

Local state is fine only for early experiments. In CI it is effectively disposable, because every runner starts clean. That means future plans cannot reliably know what was already imported or applied.

Local state is also awkward for collaboration: two operators can run different state copies and drift away from each other.

## Remote state

Use remote state before importing real Timeweb resources.

For this platform, production state uses the existing Timeweb Object Storage bucket:

```hcl
terraform {
  backend "s3" {
    bucket = "db202587-tactical-heroes"
    key    = "core-platform/production.tfstate"
    region = "ru-1"

    endpoints = {
      s3 = "https://s3.twcstorage.ru"
    }

    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    use_lockfile                = true
  }
}
```

GitHub Actions reads the S3 credentials from repository secrets:

```text
TOFU_STATE_ACCESS_KEY
TOFU_STATE_SECRET_KEY
```

The bucket itself is referenced as a `twc_s3_bucket` data source for now. It is not managed as a resource yet because the current provider docs do not document import syntax for `twc_s3_bucket`; managing the state bucket from the same state also deserves an explicit `prevent_destroy` review before we turn it into a resource.

Enable bucket versioning if Timeweb Object Storage supports it for this bucket.
