# OpenSearch Managed Domain with S3 Vectors engine for hybrid search (BM25 + kNN)

locals {
  opensearch_domain = var.deploy_id != "" ? "${var.project_name}-seg-${var.deploy_id}" : "${var.project_name}-segments"
}

resource "aws_opensearch_domain" "segments" {
  domain_name    = local.opensearch_domain
  engine_version = "OpenSearch_2.19"

  cluster_config {
    instance_type  = "or1.medium.search"
    instance_count = 1
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = 20
  }

  aiml_options {
    s3_vectors_engine {
      enabled = true
    }
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = false
    master_user_options {
      master_user_arn = aws_iam_role.lambda_exec.arn
    }
  }

  # Allow both Lambda role and account users
  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "*" }
      Action    = "es:*"
      Resource  = "arn:aws:es:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:domain/${local.opensearch_domain}/*"
      Condition = {
        StringEquals = {
          "aws:PrincipalAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}
