# /// script
# requires-python = ">=3.14"
# dependencies = ["boto3>=1.40,<2"]
# ///

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

KEY_PREFIX = "tsunagudake/daily/"


def upload_backup(archive: Path) -> None:
    if not re.fullmatch(
        r"tsunagudake-\d{8}T\d{6}Z-[A-Za-z0-9]{8}\.sql\.gz", archive.name
    ):
        raise ValueError("バックアップのファイル名を確認してください。")
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("バックアップファイルを確認してください。")

    endpoint = os.environ.get("BACKUP_S3_ENDPOINT", "")
    bucket = os.environ.get("BACKUP_S3_BUCKET", "")
    url = urlsplit(endpoint)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or not bucket
    ):
        raise ValueError("バックアップ先の HTTPS URL とバケットを確認してください。")

    access_key = os.environ.get("BACKUP_S3_ACCESS_KEY_ID")
    secret_key = os.environ.get("BACKUP_S3_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        raise ValueError("バックアップ専用のストレージ鍵を設定してください。")

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ.get("BACKUP_S3_REGION", "auto"),
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            connect_timeout=10,
            read_timeout=60,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            s3={"addressing_style": "path"},
        ),
    )
    with archive.open("rb") as stream:
        client.upload_fileobj(
            stream,
            bucket,
            KEY_PREFIX + archive.name,
            ExtraArgs={"ContentType": "application/gzip"},
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MySQL の控えを専用バケットに送ります。"
    )
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        upload_backup(args.archive)
    except BotoCoreError, ClientError, S3UploadFailedError, OSError, ValueError:
        # Provider responses can contain endpoints, bucket names or credentials.
        print(
            "バックアップを送信できませんでした。サーバー内の控えは残っています。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
