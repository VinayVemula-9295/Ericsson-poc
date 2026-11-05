terraform {
  # Bucket/key/region are supplied at init time. Credentials come from the
  # controller's AWS SDK credential chain, never from backend configuration.
  backend "s3" {}
}
