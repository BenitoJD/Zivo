"""MinIO / S3 client. This service is the only writer of object bytes."""

from __future__ import annotations

import json
import logging
import re
import uuid

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import get_settings
from app.engine_runtime import choose, pick

logger = logging.getLogger(__name__)
settings = get_settings()


def _s3_client(*, endpoint: str, secure: bool):
    return boto3.client(
        "s3",
        endpoint_url=f"{choose(secure, 'https', 'http')}://{endpoint}",
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _internal_client():
    return _s3_client(endpoint=settings.minio_endpoint, secure=settings.minio_secure)


def _presign_client():
    return _s3_client(
        endpoint=settings.minio_presign_endpoint,
        secure=settings.minio_presign_secure,
    )


def ensure_bucket() -> None:
    client = _internal_client()
    try:
        client.head_bucket(Bucket=settings.minio_bucket)
    except Exception:
        client.create_bucket(Bucket=settings.minio_bucket)


def safe_storage_filename(filename: str) -> str:
    """Sanitize a user-provided filename for inclusion in an object key."""
    base = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._-")
    return safe[:200] or "upload"


def build_storage_key(account_id: uuid.UUID | None, filename: str) -> str:
    prefix = choose(account_id is not None, f"users/{account_id}", "demo")
    return f"{prefix}/{uuid.uuid4()}/{safe_storage_filename(filename)}"


def put_object(storage_key: str, data: bytes, content_type: str) -> None:
    client = _internal_client()
    client.put_object(
        Bucket=settings.minio_bucket,
        Key=storage_key,
        Body=data,
        ContentType=content_type,
    )


def start_multipart_upload(storage_key: str, content_type: str) -> str:
    client = _internal_client()
    resp = client.create_multipart_upload(
        Bucket=settings.minio_bucket,
        Key=storage_key,
        ContentType=content_type,
    )
    return resp["UploadId"]


def upload_multipart_part(storage_key: str, upload_id: str, part_number: int, data: bytes) -> str:
    client = _internal_client()
    resp = client.upload_part(
        Bucket=settings.minio_bucket,
        Key=storage_key,
        UploadId=upload_id,
        PartNumber=part_number,
        Body=data,
    )
    return resp["ETag"]


def complete_multipart_upload(storage_key: str, upload_id: str, parts: list[dict]) -> None:
    client = _internal_client()
    client.complete_multipart_upload(
        Bucket=settings.minio_bucket,
        Key=storage_key,
        UploadId=upload_id,
        MultipartUpload={"Parts": parts},
    )


def abort_multipart_upload(storage_key: str, upload_id: str) -> None:
    client = _internal_client()
    client.abort_multipart_upload(
        Bucket=settings.minio_bucket,
        Key=storage_key,
        UploadId=upload_id,
    )


def fetch_object(storage_key: str) -> bytes:
    client = _internal_client()
    resp = client.get_object(Bucket=settings.minio_bucket, Key=storage_key)
    return resp["Body"].read()


def put_json(storage_key: str, data: object) -> None:
    body = json.dumps(data).encode("utf-8")
    put_object(storage_key, body, "application/json")


def get_json(storage_key: str) -> object:
    raw = fetch_object(storage_key)
    return json.loads(raw.decode("utf-8"))


def delete_object(storage_key: str) -> None:
    client = _internal_client()
    client.delete_object(Bucket=settings.minio_bucket, Key=storage_key)


def object_exists(storage_key: str) -> bool:
    client = _internal_client()
    try:
        client.head_object(Bucket=settings.minio_bucket, Key=storage_key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        return pick(code in {"404", "NoSuchKey", "NotFound"}, lambda: False, lambda: (
            logger.debug("head_object failed for %s", storage_key, exc_info=True) or False
        ))
    except Exception:
        logger.debug("head_object failed for %s", storage_key, exc_info=True)
        return False


def presigned_get_url(storage_key: str, expires: int = 3600) -> str:
    client = _presign_client()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.minio_bucket,
            "Key": storage_key,
            "ResponseContentType": "application/octet-stream",
            "ResponseContentDisposition": "attachment",
        },
        ExpiresIn=expires,
    )
