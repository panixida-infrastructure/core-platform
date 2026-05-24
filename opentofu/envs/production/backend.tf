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
