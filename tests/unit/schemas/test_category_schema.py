from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.category_schema import CategoryResponse


def test_category_response_missing_id():
    with pytest.raises(ValidationError):
        CategoryResponse(
            name="Electronics",
            description="Description for electronics",
            total_products=10,
        )


def test_category_response_missing_name():
    with pytest.raises(ValidationError):
        CategoryResponse(
            id=uuid4(),
            description="Description for electronics",
            total_products=10,
        )


def test_category_response_invalid_uuid():
    with pytest.raises(ValidationError):
        CategoryResponse(
            id="not-a-uuid",
            name="Electronics",
            description="Description for electronics",
            total_products=10,
        )


def test_category_response_optional_description():
    with pytest.raises(ValidationError):
        CategoryResponse(
            id=uuid4(),
            name="Electronics",
            total_products=10,
        )


def test_category_response_missing_total_products():
    with pytest.raises(ValidationError):
        CategoryResponse(
            id=uuid4(),
            name="Electronics",
            description="Description for electronics",
        )
