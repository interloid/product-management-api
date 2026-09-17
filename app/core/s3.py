from typing import Any
from urllib.parse import quote

from fastapi import Request

from app.core.settings import settings


class S3Service:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.bucket_name = settings.S3_BUCKET_NAME

    async def upload_file(
        self, data: bytes, object_key: str, content_type: str
    ) -> None:

        await self.client.put_object(
            Bucket=self.bucket_name,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )

    async def delete_file(self, object_key: str) -> None:

        await self.client.delete_object(
            Bucket=self.bucket_name,
            Key=object_key,
        )

    async def generate_cloudfront_urls(
        self,
        object_keys: list[str],
    ) -> dict[str, str]:

        base_url = str(settings.CLOUDFRONT_BASE_URL).rstrip("/")

        return {
            object_key: (f"{base_url}/{quote(object_key.lstrip('/'), safe='/')}")
            for object_key in object_keys
        }


def get_s3_service(request: Request) -> S3Service:
    return S3Service(client=request.app.state.s3)
