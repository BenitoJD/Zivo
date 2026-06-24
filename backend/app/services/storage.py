import json
import re
import uuid

import boto3
from botocore.client import Config

from app.config import get_settings

settings = get_settings()


def _s3_client(*, endpoint: str, secure: bool):
    return boto3.client(
        "s3",
        endpoint_url=f"{'https' if secure else 'http'}://{endpoint}",
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


def slugify_filename(filename: str) -> str:
    base = filename.rsplit(".", 1)[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return slug[:48] or "document"


def _safe_storage_filename(filename: str) -> str:
    """Sanitize a user-provided filename for inclusion in an object key.

    Strips path separators and ``..`` segments so a malicious filename can't
    escape its parent prefix (S3 keys are flat, but defensive sanitization
    keeps logs, listings, and presigned URLs predictable).
    """
    # Drop any directory components — keep only the basename.
    base = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # Replace anything that's not a safe filename character.
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._-")
    return safe[:200] or "upload"


def save_upload(account_id: uuid.UUID | None, filename: str, data: bytes, content_type: str) -> str:
    prefix = f"users/{account_id}" if account_id else "demo"
    key = f"{prefix}/{uuid.uuid4()}/{_safe_storage_filename(filename)}"
    client = _internal_client()
    client.put_object(Bucket=settings.minio_bucket, Key=key, Body=data, ContentType=content_type)
    return key


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
    client = _internal_client()
    client.put_object(
        Bucket=settings.minio_bucket,
        Key=storage_key,
        Body=body,
        ContentType="application/json",
    )


def get_json(storage_key: str) -> object:
    raw = fetch_object(storage_key)
    return json.loads(raw.decode("utf-8"))


def delete_object(storage_key: str) -> None:
    client = _internal_client()
    client.delete_object(Bucket=settings.minio_bucket, Key=storage_key)


def presigned_get_url(storage_key: str, expires: int = 3600) -> str:
    client = _presign_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.minio_bucket, "Key": storage_key},
        ExpiresIn=expires,
    )


def ingest_tmp_key(document_id: uuid.UUID, stage: str) -> str:
    return f"tmp/ingest/{document_id}/{stage}.json"
