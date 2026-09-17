from app.schemas.common import BaseSchema


class ProductImageUploadPayload(BaseSchema):
    image_id: str
    staging_key: str
    extension: str
    content_type: str
    content_hash: str
    is_primary: bool
