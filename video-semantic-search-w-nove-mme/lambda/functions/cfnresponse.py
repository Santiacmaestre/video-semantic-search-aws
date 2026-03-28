"""CloudFormation custom resource response helper.

Sends SUCCESS/FAILED responses back to CloudFormation's pre-signed S3 URL.
"""
import json
import urllib.request

SUCCESS = "SUCCESS"
FAILED = "FAILED"


def send(event, context, response_status, response_data, physical_resource_id=None, no_echo=False):
    response_url = event['ResponseURL']
    response_body = json.dumps({
        'Status': response_status,
        'Reason': f"See CloudWatch Log Stream: {context.log_stream_name}",
        'PhysicalResourceId': physical_resource_id or context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'NoEcho': no_echo,
        'Data': response_data,
    }).encode('utf-8')

    req = urllib.request.Request(response_url, data=response_body, method='PUT')
    req.add_header('Content-Type', '')
    req.add_header('Content-Length', str(len(response_body)))
    urllib.request.urlopen(req)
