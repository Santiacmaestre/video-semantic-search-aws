# S3 Vectors setup via Python SDK
# Creates vector bucket only. Indices are created per-project at runtime.

resource "null_resource" "s3_vectors_setup" {
  triggers = {
    bucket_name = local.vector_bucket_name
  }

  provisioner "local-exec" {
    command = <<-EOT
      python3 -c "
import boto3
session = boto3.Session(profile_name='${var.aws_profile}', region_name='${var.aws_region}')
s3v = session.client('s3vectors')
bucket = '${local.vector_bucket_name}'
try:
    s3v.create_vector_bucket(vectorBucketName=bucket)
    print(f'Created vector bucket: {bucket}')
except Exception as e:
    print(f'Vector bucket: {e}')
print('Indices are created per-project at runtime.')
"
    EOT
  }

  depends_on = [
    aws_dynamodb_table.videos,
    aws_s3_bucket.videos
  ]
}
