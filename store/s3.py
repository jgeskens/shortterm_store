from botocore.config import Config
import logging
import boto3
from botocore.exceptions import ClientError
import requests


s3_config = Config(
    region_name='eu-central-1',
    signature_version='s3v4',
    retries={
        'max_attempts': 10,
    },
    s3={'addressing_style': 'path'}
)


def create_presigned_post(bucket_name, object_name,
                          fields=None, conditions=None, expiration=3600):
    """Generate a presigned URL S3 POST request to upload a file

    :param bucket_name: string
    :param object_name: string
    :param fields: Dictionary of prefilled form fields
    :param conditions: List of conditions to include in the policy
    :param expiration: Time in seconds for the presigned URL to remain valid
    :return: Dictionary with the following keys:
        url: URL to post to
        fields: Dictionary of form fields and values to submit with the POST
    :return: None if error.
    """

    # Generate a presigned S3 POST URL
    s3_client = boto3.client('s3', config=s3_config)
    try:
        response = s3_client.generate_presigned_post(bucket_name,
                                                     object_name,
                                                     Fields=fields,
                                                     Conditions=conditions,
                                                     ExpiresIn=expiration)
    except ClientError as e:
        logging.error(e)
        return None

    # The response contains the presigned URL and required fields
    return response


def upload_file():
    # Generate a presigned S3 POST URL
    object_name = 'test_presigned_upload.txt'
    response = create_presigned_post('geskens-shorttermstore-data', object_name)
    if response is None:
        exit(1)

    # Demonstrate how another Python program can use the presigned URL to upload a file
    with open(object_name, 'rb') as f:
        files = {'file': (object_name, f)}
        http_response = requests.post(response['url'], data=response['fields'], files=files)
    # If successful, returns HTTP status code 204
    print(f'File upload HTTP status code: {http_response.status_code}')
