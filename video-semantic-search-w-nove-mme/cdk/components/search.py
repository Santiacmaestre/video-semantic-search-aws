from constructs import Construct
from aws_cdk import (
    aws_opensearchservice as opensearch,
    aws_iam as iam,
    aws_ec2 as ec2,
)


class SearchConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        account_id: str,
        lambda_role: iam.IRole,
    ) -> None:
        super().__init__(scope, id)

        # OR1 instance type has S3 Vectors engine built-in
        self.domain = opensearch.Domain(
            self, "SegmentsDomain",
            version=opensearch.EngineVersion.OPENSEARCH_2_19,
            capacity=opensearch.CapacityConfig(
                data_node_instance_type="or1.medium.search",
                data_nodes=1,
            ),
            ebs=opensearch.EbsOptions(
                volume_type=ec2.EbsDeviceVolumeType.GP3,
                volume_size=20,
            ),
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            node_to_node_encryption=True,
            enforce_https=True,
            tls_security_policy=opensearch.TLSSecurityPolicy.TLS_1_2,
            fine_grained_access_control=opensearch.AdvancedSecurityOptions(
                master_user_arn=lambda_role.role_arn,
            ),
            access_policies=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    principals=[iam.ArnPrincipal(lambda_role.role_arn)],
                    actions=["es:ESHttp*"],
                    # Resource-based policy on domain itself; IAM policy in compute.py
                    # scopes to this specific domain ARN via add_opensearch_permissions()
                    resources=["*"],
                ),
            ],
        )

        # Enable S3 Vectors engine via L1 escape hatch (not yet on L2 Domain)
        cfn_domain = self.domain.node.default_child
        cfn_domain.aiml_options = opensearch.CfnDomain.AIMLOptionsProperty(
            s3_vectors_engine=opensearch.CfnDomain.S3VectorsEngineProperty(enabled=True),
        )

        self.domain_endpoint = self.domain.domain_endpoint
