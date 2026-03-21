from __future__ import annotations

import json
from typing import Any

from ..config import Settings, get_settings


class SpacesStorageError(RuntimeError):
    """Raised when DigitalOcean Spaces operations cannot be completed."""


class SpacesStorageConfigError(SpacesStorageError):
    """Raised when DigitalOcean Spaces is not configured or the client is unavailable."""


class SpacesStorageService:
    """Thin wrapper around DigitalOcean Spaces operations used by cloud backup."""

    def __init__(self, settings: Settings | None = None, s3_client: Any | None = None):
        self.settings = settings or get_settings()
        self._s3_client = s3_client

    def is_configured(self) -> bool:
        return bool(
            (self.settings.spaces_bucket_backups or "").strip()
            and (self.settings.spaces_access_key_id or "").strip()
            and (self.settings.spaces_secret_access_key or "").strip()
            and (self.settings.spaces_endpoint_url or "").strip()
        )

    def _get_s3_client(self) -> Any:
        if self._s3_client is not None:
            return self._s3_client

        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError as exc:
            raise SpacesStorageConfigError(
                "boto3 package is not installed"
            ) from exc

        endpoint_url = (self.settings.spaces_endpoint_url or "").strip()
        region = (self.settings.spaces_region or "").strip() or None
        access_key_id = (self.settings.spaces_access_key_id or "").strip()
        secret_access_key = (self.settings.spaces_secret_access_key or "").strip()
        addressing_style = (self.settings.spaces_addressing_style or "virtual").strip().lower() or "virtual"

        if not endpoint_url:
            raise SpacesStorageConfigError("DigitalOcean Spaces endpoint URL is not configured")
        if not access_key_id or not secret_access_key:
            raise SpacesStorageConfigError("DigitalOcean Spaces credentials are not configured")

        try:
            session = boto3.session.Session()
            self._s3_client = session.client(
                "s3",
                region_name=region,
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                config=BotoConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": addressing_style},
                ),
            )
        except Exception as exc:
            raise SpacesStorageConfigError(
                "Failed to create DigitalOcean Spaces client"
            ) from exc
        return self._s3_client

    def _normalize_target(self, bucket_name: str, object_key: str) -> tuple[str, str]:
        normalized_bucket_name = (bucket_name or "").strip()
        normalized_object_key = (object_key or "").strip()

        if not normalized_bucket_name:
            raise SpacesStorageConfigError("Spaces backup bucket is not configured")
        if not normalized_object_key:
            raise SpacesStorageConfigError("Spaces object key is required")

        return normalized_bucket_name, normalized_object_key

    def _build_presign_params(
        self,
        bucket_name: str,
        object_key: str,
        *,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        normalized_bucket_name, normalized_object_key = self._normalize_target(bucket_name, object_key)
        params: dict[str, Any] = {
            "Bucket": normalized_bucket_name,
            "Key": normalized_object_key,
        }
        if content_type:
            params["ContentType"] = content_type
        return params

    def create_presigned_upload_url(
        self,
        bucket_name: str,
        object_key: str,
        *,
        content_type: str = "application/zip",
        size: int | None = None,
    ) -> str:
        del size
        client = self._get_s3_client()
        ttl_seconds = max(60, int(self.settings.spaces_signed_url_ttl_seconds))
        params = self._build_presign_params(bucket_name, object_key, content_type=content_type)

        try:
            return client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=ttl_seconds,
                HttpMethod="PUT",
            )
        except Exception as exc:
            raise SpacesStorageError("Failed to generate Spaces signed upload URL") from exc

    def upload_json(self, bucket_name: str, object_key: str, payload: dict[str, Any]) -> None:
        normalized_bucket_name, normalized_object_key = self._normalize_target(bucket_name, object_key)
        client = self._get_s3_client()
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        try:
            client.put_object(
                Bucket=normalized_bucket_name,
                Key=normalized_object_key,
                Body=body,
                ContentType="application/json",
            )
        except Exception as exc:
            raise SpacesStorageError("Failed to upload Spaces JSON object") from exc

    def upload_bytes(
        self,
        bucket_name: str,
        object_key: str,
        payload: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        normalized_bucket_name, normalized_object_key = self._normalize_target(bucket_name, object_key)
        client = self._get_s3_client()
        try:
            client.put_object(
                Bucket=normalized_bucket_name,
                Key=normalized_object_key,
                Body=payload,
                ContentType=content_type,
            )
        except Exception as exc:
            raise SpacesStorageError("Failed to upload Spaces binary object") from exc

    def download_json(self, bucket_name: str, object_key: str) -> dict[str, Any]:
        normalized_bucket_name, normalized_object_key = self._normalize_target(bucket_name, object_key)
        client = self._get_s3_client()
        try:
            response = client.get_object(
                Bucket=normalized_bucket_name,
                Key=normalized_object_key,
            )
            raw = response["Body"].read().decode("utf-8")
        except Exception as exc:
            raise SpacesStorageError("Failed to download Spaces JSON object") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SpacesStorageError("Downloaded Spaces object is not valid JSON") from exc

    def download_bytes(self, bucket_name: str, object_key: str) -> bytes:
        normalized_bucket_name, normalized_object_key = self._normalize_target(bucket_name, object_key)
        client = self._get_s3_client()
        try:
            response = client.get_object(
                Bucket=normalized_bucket_name,
                Key=normalized_object_key,
            )
            return response["Body"].read()
        except Exception as exc:
            raise SpacesStorageError("Failed to download Spaces binary object") from exc

    def generate_download_url(self, bucket_name: str, object_key: str) -> str:
        client = self._get_s3_client()
        ttl_seconds = max(60, int(self.settings.spaces_signed_url_ttl_seconds))
        params = self._build_presign_params(bucket_name, object_key)

        try:
            return client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=ttl_seconds,
                HttpMethod="GET",
            )
        except Exception as exc:
            raise SpacesStorageError("Failed to generate Spaces signed download URL") from exc
