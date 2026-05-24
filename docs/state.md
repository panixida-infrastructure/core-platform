# OpenTofu State

OpenTofu state should not live only on a GitHub Actions runner.

## Local state

Local state is fine only for early experiments. In CI it is effectively disposable, because every runner starts clean. That means future plans cannot reliably know what was already imported or applied.

Local state is also awkward for collaboration: two operators can run different state copies and drift away from each other.

## Remote state

Use remote state before importing real Timeweb resources.

For this platform, the preferred next step is an S3-compatible backend, for example Timeweb Object Storage:

```hcl
terraform {
  backend "s3" {
    endpoint                    = "https://s3.timeweb.cloud"
    bucket                      = "panixida-tofu-state"
    key                         = "core-platform/production.tfstate"
    region                      = "ru-1"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    use_lockfile                = true
  }
}
```

Enable bucket versioning if Timeweb Object Storage supports it for the selected bucket.
