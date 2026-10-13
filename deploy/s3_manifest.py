"""Print a JSON-lines manifest of the S3 upload bucket with presigned GET URLs.

Runs where AWS credentials are available. Pipe the output into fetch_s3_manifest.py on the
server, so the server never needs AWS credentials.
"""
import argparse
import json

import boto3
from botocore.config import Config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', default='geskens-shorttermstore-data')
    parser.add_argument('--profile', default='geskens')
    parser.add_argument('--region', default='eu-central-1')
    parser.add_argument('--expires', type=int, default=6 * 3600, help='URL lifetime in seconds')
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3 = session.client('s3', config=Config(signature_version='s3v4'))
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=args.bucket):
        for obj in page.get('Contents', []):
            url = s3.generate_presigned_url(
                'get_object', Params={'Bucket': args.bucket, 'Key': obj['Key']}, ExpiresIn=args.expires
            )
            print(json.dumps({
                'key': obj['Key'],
                'size': obj['Size'],
                'last_modified': obj['LastModified'].timestamp(),
                'url': url,
            }))


if __name__ == '__main__':
    main()
