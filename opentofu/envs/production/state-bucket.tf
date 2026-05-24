data "twc_s3_bucket" "tofu_state" {
  name = local.tofu_state_bucket_name
}
