# Toy OpenTofu module for the Atlantis learning experiment. Uses only the `local` provider, so
# `tofu plan`/`tofu apply` create nothing outside the Atlantis Pod's own checked-out working
# directory: no cloud account, no credentials, no billing, no real infrastructure. Same
# "prove the mechanism, zero real cost" pattern used elsewhere for early IaC validation.
terraform {
  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

resource "local_file" "hello" {
  filename = "${path.module}/hello.txt"
  content  = "Hello from the Atlantis demo, managed by OpenTofu (triggered via simulated webhook).\n"
}
