"""Private source-payload storage backed by S3."""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

import boto3
from botocore.exceptions import ClientError


def get_s3_bucket() -> str:
    bucket = _setting("MEMORY_S3_BUCKET")
    if not bucket or not bucket.strip():
        raise RuntimeError("Missing MEMORY_S3_BUCKET; S3 storage is required.")
    return bucket.strip()


@lru_cache(maxsize=1)
def get_s3_client() -> Any:
    """Build an S3 client using boto3's standard AWS credential chain."""

    options: dict[str, str] = {}
    region = _setting("MEMORY_S3_REGION") or _setting("AWS_REGION")
    endpoint_url = _setting("MEMORY_S3_ENDPOINT_URL")
    access_key_id = _setting("AWS_ACCESS_KEY_ID")
    secret_access_key = _setting("AWS_SECRET_ACCESS_KEY")
    session_token = _setting("AWS_SESSION_TOKEN")
    if bool(access_key_id) != bool(secret_access_key):
        raise RuntimeError(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be configured together."
        )
    if region:
        options["region_name"] = region
    if endpoint_url:
        options["endpoint_url"] = endpoint_url
    if access_key_id and secret_access_key:
        options["aws_access_key_id"] = access_key_id
        options["aws_secret_access_key"] = secret_access_key
    if session_token:
        options["aws_session_token"] = session_token
    return boto3.client("s3", **options)


def read_storage_object(storage_object_key: str) -> bytes:
    """Read a private source payload from S3."""

    _validate_storage_object_key(storage_object_key)
    bucket = get_s3_bucket()
    return _read_s3_object(bucket, _s3_object_key(storage_object_key))


def write_storage_object(storage_object_key: str, content: bytes) -> None:
    """Store content without silently changing an idempotent source."""

    _validate_storage_object_key(storage_object_key)
    bucket = get_s3_bucket()
    _write_s3_object(bucket, _s3_object_key(storage_object_key), content)


def _read_s3_object(bucket: str, object_key: str) -> bytes:
    try:
        response = get_s3_client().get_object(Bucket=bucket, Key=object_key)
    except ClientError as exc:
        if _client_error_status(exc) == 404 or _client_error_code(exc) in {
            "NoSuchKey",
            "NotFound",
        }:
            raise FileNotFoundError(
                f"S3 object s3://{bucket}/{object_key} does not exist."
            ) from exc
        raise

    content = response["Body"].read()
    if not isinstance(content, bytes):
        raise TypeError("S3 get_object response body did not return bytes.")
    return content


def _write_s3_object(bucket: str, object_key: str, content: bytes) -> None:
    digest = hashlib.sha256(content).hexdigest()
    request: dict[str, Any] = {
        "Bucket": bucket,
        "Key": object_key,
        "Body": content,
        "ContentType": "application/octet-stream",
        "Metadata": {"sha256": digest},
        "IfNoneMatch": "*",
    }
    request.update(_s3_encryption_options())

    try:
        get_s3_client().put_object(**request)
        return
    except ClientError as exc:
        if _client_error_status(exc) not in {409, 412} and _client_error_code(
            exc
        ) not in {"ConditionalRequestConflict", "PreconditionFailed"}:
            raise

    existing_content = _read_s3_object(bucket, object_key)
    if existing_content != content:
        raise ValueError(
            "The idempotency key is already associated with different content."
        )


def _s3_encryption_options() -> dict[str, str]:
    encryption = _setting("MEMORY_S3_SERVER_SIDE_ENCRYPTION")
    kms_key_id = _setting("MEMORY_S3_KMS_KEY_ID")
    if kms_key_id:
        return {
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": kms_key_id,
        }
    if encryption:
        return {"ServerSideEncryption": encryption}
    return {}


def _s3_object_key(storage_object_key: str) -> str:
    prefix = (_setting("MEMORY_S3_PREFIX") or "").strip("/")
    return f"{prefix}/{storage_object_key}" if prefix else storage_object_key


def _validate_storage_object_key(storage_object_key: str) -> None:
    path = PurePosixPath(storage_object_key)
    if (
        not storage_object_key
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in storage_object_key
    ):
        raise ValueError(f"Invalid storage object key: {storage_object_key!r}.")


def _client_error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _client_error_status(exc: ClientError) -> int | None:
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status if isinstance(status, int) else None


def _setting(name: str) -> str | None:
    return os.getenv(name)
