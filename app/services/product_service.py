import asyncio
import hashlib
from decimal import Decimal
from uuid import UUID, uuid4

from arq.connections import ArqRedis
from fastapi import UploadFile
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import (
    ProductImageConstants,
    ProductStatusEnum,
)
from app.core.logging import get_logger
from app.core.s3 import S3Service
from app.exceptions.custom import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from app.models.product_model import Product
from app.repositories.category_repo import CategoryRepository
from app.repositories.product_image_repo import ProductImageRepository
from app.repositories.product_repo import ProductRepository
from app.schemas.image_jobs_schema import ProductImageUploadPayload
from app.schemas.product_schema import ProductCreate, ProductUpdate
from app.services.base_service import BaseService
from app.services.product_image_service import ProductImageService

logger = get_logger(__name__)


class ProductService(BaseService[Product]):
    SORT_FIELDS = {
        "name": Product.name,
        "sku": Product.sku,
        "category": Product.category_id,
        "price": Product.price,
        "stock": Product.stock,
        "status": Product.status,
        "updated": Product.updated_at,
    }

    DEFAULT_SORT = "updated"

    def __init__(
        self, db: AsyncSession, s3_service: S3Service, arq_pool: ArqRedis
    ) -> None:
        super().__init__(db)
        self.arq_pool = arq_pool

        self.product_repo = ProductRepository(db)
        self.category_repo = CategoryRepository(db)
        self.product_image_service = ProductImageService(
            product_image_repo=ProductImageRepository(db),
            s3_service=s3_service,
        )

    async def _validate_product_images(
        self,
        images: list[UploadFile],
    ) -> None:

        if len(images) > ProductImageConstants.MAX_IMAGES:
            logger.warning(
                "Maximum %s images are allowed",
                ProductImageConstants.MAX_IMAGES,
            )
            raise BadRequestException(
                message=(
                    f"Maximum {ProductImageConstants.MAX_IMAGES} images are allowed"
                ),
            )

        for image in images:
            if not image.filename:
                logger.warning(
                    "Image filename is required | filename=%s",
                    image.filename,
                )
                raise BadRequestException(
                    message="Image filename is required",
                )

            if not image.content_type:
                logger.warning(
                    "Content type could not be determined | filename=%s",
                    image.filename,
                )
                raise BadRequestException(
                    message=(
                        f"Content type could not be determined for '{image.filename}'"
                    ),
                )

            if image.content_type not in ProductImageConstants.ALLOWED_CONTENT_TYPES:
                logger.warning(
                    "Unsupported image type | content_type=%s",
                    image.content_type,
                )
                raise BadRequestException(
                    message=(
                        f"Unsupported image type '{image.content_type}'. "
                        "Allowed types: JPEG, PNG, and WebP"
                    ),
                )
            if image.size is None:
                logger.warning(
                    "Could not determine image size | filename=%s",
                    image.filename,
                )
                raise BadRequestException(
                    message=(f"Could not determine size for '{image.filename}'"),
                )

            if image.size > ProductImageConstants.MAX_FILE_SIZE:
                logger.warning(
                    "Image exceeds maximum size | filename=%s | size=%s",
                    image.filename,
                    image.size,
                )
                raise BadRequestException(
                    message=(
                        f"Image '{image.filename}' exceeds the maximum size of 5 MB"
                    ),
                )

    async def _cleanup_s3(self, object_keys: list[str]) -> None:
        if not object_keys:
            return
        results = await asyncio.gather(
            *[
                self.product_image_service.s3_service.delete_file(
                    object_key=object_key,
                )
                for object_key in object_keys
            ],
            return_exceptions=True,
        )
        for object_key, result in zip(
            object_keys,
            results,
            strict=True,
        ):
            if isinstance(result, Exception):
                logger.error(
                    "Failed to clean up S3 object | object_key=%s | error=%s",
                    object_key,
                    result,
                )

    async def _hash_product_images(
        self,
        images: list[UploadFile],
    ) -> list[str]:

        content_hashes: list[str] = []

        for image in images:
            digest = hashlib.sha256()
            await image.seek(0)

            while chunk := await image.read(1024 * 1024):
                digest.update(chunk)

            await image.seek(0)
            content_hashes.append(digest.hexdigest())

        if len(content_hashes) != len(set(content_hashes)):
            raise ConflictException(
                message="Duplicate images are not allowed",
            )

        return content_hashes

    async def create_product(
        self, payload: ProductCreate, images: list[UploadFile]
    ) -> Product:

        await self._validate_product_images(images)
        content_hashes = await self._hash_product_images(images)

        existing_product = await self.product_repo.get_by_sku(sku=payload.sku)

        if existing_product is not None:
            logger.warning("Product with this SKU already exists | sku=%s", payload.sku)
            raise ConflictException(
                message="Product with this SKU already exists",
            )

        category_name = payload.category_name.strip()

        category = await self.category_repo.get_by_name(name=category_name)

        if category is None:
            logger.warning("Category not found | category=%s", category)
            raise NotFoundException(message="Category not found")

        product = Product(
            name=payload.name,
            sku=payload.sku,
            category_id=category.id,
            price=payload.price,
            stock=payload.stock,
            status=payload.status,
            description=payload.description,
        )

        staging_object_keys: list[str] = []
        job_images: list[ProductImageUploadPayload] = []

        try:
            product = await self.product_repo.create(
                product=product,
            )

            for index, (image, content_hash) in enumerate(
                zip(images, content_hashes, strict=True)
            ):
                image_id = uuid4()

                content_type = image.content_type
                if content_type is None:
                    raise BadRequestException(
                        message="Image content type is required",
                    )

                extension = ProductImageConstants.EXTENSION_BY_CONTENT_TYPE.get(
                    content_type
                )

                if extension is None:
                    raise BadRequestException(
                        message=f"Unsupported image type '{content_type}'",
                    )

                staging_key = (
                    f"staging/products/{product.id}/images/{image_id}.{extension}"
                )

                staging_object_keys.append(staging_key)

                data = await image.read()

                await self.product_image_service.s3_service.upload_file(
                    data=data,
                    object_key=staging_key,
                    content_type=content_type,
                )

                job_images.append(
                    ProductImageUploadPayload(
                        image_id=str(image_id),
                        staging_key=staging_key,
                        extension=extension,
                        content_type=content_type,
                        content_hash=content_hash,
                        is_primary=(index == 0),
                    )
                )

            await self.db.commit()

            await self.arq_pool.enqueue_job(
                "upload_product_images",
                str(product.id),
                [image.model_dump(mode="json") for image in job_images],
                _expires=86_400,
            )

            product = await self.product_repo.get_by_id(
                product_id=product.id,
            )

            if product is None:
                logger.warning("Product not found after creation")
                raise RuntimeError("Product not found after creation")

            return product

        except IntegrityError as exc:
            await self.db.rollback()
            await self._cleanup_s3(staging_object_keys)
            raise ConflictException(
                message=("Product already exists"),
            ) from exc

        except Exception:
            await self.db.rollback()
            await self._cleanup_s3(staging_object_keys)
            raise

    async def get_product(self, product_id: UUID) -> Product:

        product = await self.product_repo.get_by_id(product_id=product_id)

        if product is None:
            logger.warning("Product not found | product_id=%s", product_id)
            raise NotFoundException(message="Product not found")

        return product

    async def list_products(
        self,
        *,
        search: str | None = None,
        category_name: str | None = None,
        status: ProductStatusEnum | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        in_stock: bool | None = None,
        sort_by: str = DEFAULT_SORT,
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[Product], int]:

        if min_price is not None and max_price is not None and min_price > max_price:
            logger.warning(
                "Minimum price cannot be greater than maximum price | "
                "min_price=%s | max_price=%s",
                min_price,
                max_price,
            )
            raise BadRequestException(
                message="Minimum price cannot be greater than maximum price",
            )

        normalized_sort_order = self.validate_sort_order(
            sort_order,
        )

        sort_column = self.resolve_sort_column(
            sort_by=sort_by,
            sort_fields=self.SORT_FIELDS,
            default_sort=self.DEFAULT_SORT,
        )

        stmt = select(Product).options(
            selectinload(Product.images), selectinload(Product.category)
        )

        if search:
            search_pattern = f"%{search.strip()}%"

            search_expression = or_(
                Product.name.ilike(search_pattern),
                Product.sku.ilike(search_pattern),
            )

            stmt = self.apply_search(
                stmt,
                search_expression=search_expression,
            )

        filters = []

        if category_name is not None:
            filters.append(
                Product.category.has(
                    name=category_name,
                ),
            )

        if status is not None:
            filters.append(
                Product.status == status,
            )

        if min_price is not None:
            filters.append(
                Product.price >= min_price,
            )

        if max_price is not None:
            filters.append(
                Product.price <= max_price,
            )

        if in_stock is True:
            filters.append(
                Product.stock > 0,
            )

        elif in_stock is False:
            filters.append(
                Product.stock == 0,
            )

        stmt = self.apply_filters(
            stmt,
            filters=filters,
        )

        stmt = self.apply_sorting(
            stmt,
            sort_column=sort_column,
            sort_order=normalized_sort_order,
        )

        return await self.paginate(
            stmt,
            page=page,
            page_size=page_size,
        )

    async def update_product(
        self,
        product_id: UUID,
        payload: ProductUpdate,
        images: list[UploadFile],
        removed_image_ids: list[UUID] | None = None,
        primary_image_id: UUID | None = None,
    ) -> Product:

        product = await self.product_repo.get_by_id(
            product_id=product_id,
        )

        if product is None:
            raise NotFoundException(
                message="Product not found",
            )

        await self._validate_product_images(images)

        removed_image_id_set = set(removed_image_ids or [])
        current_image_ids = {image.id for image in product.images}

        unknown_image_ids = removed_image_id_set - current_image_ids

        if unknown_image_ids:
            raise NotFoundException(
                message="Product image not found",
            )

        if primary_image_id is not None:
            if primary_image_id not in current_image_ids:
                raise NotFoundException(
                    message="Primary image not found for this product",
                )

            if primary_image_id in removed_image_id_set:
                raise BadRequestException(
                    message="Primary image cannot also be removed",
                )

        retained_images = [
            image for image in product.images if image.id not in removed_image_id_set
        ]

        retained_primary_image_id = next(
            (image.id for image in retained_images if image.is_primary),
            None,
        )

        resulting_image_count = len(retained_images) + len(images)

        if resulting_image_count > ProductImageConstants.MAX_IMAGES:
            raise BadRequestException(
                message=(
                    f"Maximum {ProductImageConstants.MAX_IMAGES} images are allowed"
                ),
            )

        content_hashes = await self._hash_product_images(images)

        retained_hashes = {
            image.content_hash
            for image in retained_images
            if image.content_hash is not None
        }

        if retained_hashes.intersection(content_hashes):
            raise ConflictException(
                message="Duplicate images are not allowed",
            )

        updates = payload.model_dump(exclude_unset=True)

        if "sku" in updates and updates["sku"] != product.sku:
            existing_product = await self.product_repo.get_by_sku(
                sku=updates["sku"],
            )

            if existing_product is not None and existing_product.id != product.id:
                raise ConflictException(
                    message="Product with this SKU already exists",
                )

        if "category_name" in updates:
            category_name = updates["category_name"].strip()

            category = await self.category_repo.get_by_name(
                name=category_name,
            )

            if category is None:
                raise NotFoundException(
                    message="Category not found",
                )

            product.category_id = category.id
            del updates["category_name"]

        for field, value in updates.items():
            setattr(product, field, value)

        images_to_delete: list[str] = []
        staging_object_keys: list[str] = []
        job_images: list[ProductImageUploadPayload] = []

        try:
            product = await self.product_repo.update(
                product=product,
            )

            if removed_image_id_set:
                images_to_remove = await self.product_image_service.get_images(
                    image_ids=list(removed_image_id_set),
                    product_id=product_id,
                )

                images_to_delete.extend(image.object_key for image in images_to_remove)

                image_repo = self.product_image_service.product_image_repo

                await image_repo.delete_by_ids_and_product(
                    image_ids=list(removed_image_id_set),
                    product_id=product_id,
                )

            if primary_image_id is not None:
                await self.product_image_service.set_primary_image(
                    image_id=primary_image_id,
                    product_id=product.id,
                )

            elif retained_primary_image_id is None and not images and retained_images:
                await self.product_image_service.set_primary_image(
                    image_id=retained_images[0].id,
                    product_id=product.id,
                )

            for index, (image, content_hash) in enumerate(
                zip(images, content_hashes, strict=True)
            ):
                image_id = uuid4()
                content_type = image.content_type
                if content_type is None:
                    raise BadRequestException(
                        message="Image content type is required",
                    )
                extension = ProductImageConstants.EXTENSION_BY_CONTENT_TYPE.get(
                    content_type
                )

                if extension is None:
                    raise BadRequestException(
                        message=(f"Unsupported image type '{content_type}'"),
                    )

                staging_key = (
                    f"staging/products/{product.id}/images/{image_id}.{extension}"
                )

                staging_object_keys.append(staging_key)

                data = await image.read()

                await self.product_image_service.s3_service.upload_file(
                    data=data,
                    object_key=staging_key,
                    content_type=content_type,
                )

                job_images.append(
                    ProductImageUploadPayload(
                        image_id=str(image_id),
                        staging_key=staging_key,
                        extension=extension,
                        content_type=content_type,
                        content_hash=content_hash,
                        is_primary=(
                            index == 0
                            and primary_image_id is None
                            and retained_primary_image_id is None
                        ),
                    )
                )

            await self.db.commit()

        except IntegrityError as exc:
            await self.db.rollback()
            await self._cleanup_s3(staging_object_keys)

            raise ConflictException(
                message=(
                    "Product could not be updated because of a conflicting resource"
                ),
            ) from exc

        except Exception:
            await self.db.rollback()
            await self._cleanup_s3(staging_object_keys)
            raise

        try:
            await self._enqueue_s3_cleanup(images_to_delete)
        except Exception:
            logger.exception("Failed to enqueue S3 cleanup after product update")

        if job_images:
            job = await self.arq_pool.enqueue_job(
                "upload_product_images",
                str(product.id),
                job_images,
                _expires=86_400,
            )

            if job is None:
                logger.error(
                    "Product image job was not queued | product_id=%s",
                    product.id,
                )
                raise RuntimeError(
                    "Product image processing could not be queued",
                )

        product = await self.product_repo.get_by_id(
            product_id=product.id,
        )

        if product is None:
            raise NotFoundException(
                message="Product not found",
            )

        await self.db.refresh(
            product,
            ["images", "category"],
        )

        return product

    async def delete_product(self, product_id: UUID) -> None:

        product = await self.product_repo.get_by_id(
            product_id=product_id,
        )

        if product is None:
            logger.warning("product not found product_id=%s", product_id)
            raise NotFoundException(message="Product not found")

        object_keys = [image.object_key for image in product.images]

        await self.product_repo.delete(product=product)
        await self.db.commit()

        try:
            await self._enqueue_s3_cleanup(object_keys)

        except Exception:
            logger.exception("Failed to enqueue S3 cleanup after product deletion")

    async def _enqueue_s3_cleanup(
        self,
        object_keys: list[str],
    ) -> None:

        if not object_keys:
            return

        await self.arq_pool.enqueue_job(
            "delete_s3_jobs",
            object_keys,
            _expires=86_400,
        )
