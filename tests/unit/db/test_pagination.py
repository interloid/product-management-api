import pytest
from sqlalchemy import column, select

from app.core.constants import PaginationEnum
from app.exceptions.custom import BadRequestException
from app.services.base_service import BaseService


def test_calculate_offset_first_page():
    result = BaseService.calculate_offset(
        page=1,
        page_size=10,
    )
    assert result == 0


def test_calculate_offset_second_page():
    result = BaseService.calculate_offset(
        page=2,
        page_size=10,
    )

    assert result == 10


def test_calculate_offset_third_page():
    result = BaseService.calculate_offset(
        page=3,
        page_size=10,
    )

    assert result == 20


@pytest.mark.parametrize(
    ("total", "page_size", "expected_pages"),
    [
        (0, 10, 0),
        (100, 10, 10),
        (101, 10, 11),
        (5, 10, 1),
        (25, 10, 3),
    ],
)
def test_calculate_total_pages(
    total: int,
    page_size: int,
    expected_pages: int,
):
    result = BaseService.calculate_total_pages(
        total=total,
        page_size=page_size,
    )

    assert result == expected_pages


@pytest.mark.parametrize(
    ("page", "page_size", "expected_message"),
    [
        (0, 10, "Page must be greater than or equal to 1"),
        (1, 0, "Page size must be greater than or equal to 1"),
        (
            1,
            PaginationEnum.MAX_PAGE_SIZE + 1,
            f"Page size must be less than or equal to {PaginationEnum.MAX_PAGE_SIZE}",
        ),
    ],
)
def test_validate_pagination_rejects_invalid_values(
    page: int,
    page_size: int,
    expected_message: str,
):
    with pytest.raises(BadRequestException) as exc_info:
        BaseService.validate_pagination(
            page=page,
            page_size=page_size,
        )

    assert str(exc_info.value) == expected_message


def test_validate_pagination_accepts_valid_values():
    result = BaseService.validate_pagination(
        page=1,
        page_size=10,
    )
    assert result is None


@pytest.mark.parametrize(
    ("sort_order", "expected_message"),
    [
        ("asc", "asc"),
        ("desc", "desc"),
        ("ASC", "asc"),
        ("DESC", "desc"),
        ("Asc", "asc"),
        ("Desc", "desc"),
        (" asc", "asc"),
        (" DESC ", "desc"),
    ],
)
def test_validate_sort_order_accepts_valid_values(
    sort_order: str,
    expected_message: str,
):
    result = BaseService.validate_sort_order(sort_order)
    assert result == expected_message


@pytest.mark.parametrize(
    "sort_order",
    [
        "",
        "invalid",
        "ascending",
        "descending",
        "random",
        "123",
        " ",
        "   ",
    ],
)
def test_validate_sort_order_rejects_invalid_values(
    sort_order: str,
):
    with pytest.raises(BadRequestException) as exc_info:
        BaseService.validate_sort_order(sort_order)

    assert str(exc_info.value) == ("Sort order must be either 'asc' or 'desc'")


@pytest.mark.parametrize(
    ("sort_by", "expected"),
    [
        ("name", "NAME_COLUMN"),
        (" price ", "PRICE_COLUMN"),
        ("PRICE", "PRICE_COLUMN"),
    ],
)
def test_resolve_sort_column_normalizes_sort_by(
    sort_by: str,
    expected: str,
):
    sort_fields = {
        "name": "NAME_COLUMN",
        "price": "PRICE_COLUMN",
    }

    result = BaseService.resolve_sort_column(
        sort_by=sort_by,
        sort_fields=sort_fields,
    )

    assert result == expected


def test_resolve_sort_column_rejects_invalid_sort_field():
    sort_fields = {
        "name": "NAME_COLUMN",
        "price": "PRICE_COLUMN",
    }

    with pytest.raises(BadRequestException) as exc_info:
        BaseService.resolve_sort_column(
            sort_by="unknown",
            sort_fields=sort_fields,
        )

    assert str(exc_info.value) == "Invalid sort field"


@pytest.mark.parametrize(
    ("sort_order", "expected_sql"),
    [
        ("asc", "ORDER BY name ASC"),
        ("desc", "ORDER BY name DESC"),
    ],
)
def test_apply_sorting(sort_order: str, expected_sql: str):
    stmt = select(column("name"))
    sort_column = column("name")

    result = BaseService.apply_sorting(
        stmt,
        sort_column=sort_column,
        sort_order=sort_order,
    )

    sql = str(result)

    assert expected_sql in sql


def test_apply_search_without_expression():
    stmt = select(column("name"))

    result = BaseService.apply_search(
        stmt,
        search_expression=None,
    )

    assert result is stmt


def test_apply_search_with_expression():
    name_column = column("name")
    stmt = select(name_column)

    search_expression = name_column.ilike("%phone%")

    result = BaseService.apply_search(
        stmt,
        search_expression=search_expression,
    )

    sql = str(result)

    assert "WHERE" in sql


@pytest.mark.parametrize(
    "filters",
    [
        None,
        [],
    ],
)
def test_apply_filters_without_filters(filters):
    stmt = select(column("name"))

    result = BaseService.apply_filters(
        stmt,
        filters=filters,
    )

    assert result is stmt


def test_apply_filters_with_single_filter():
    price_column = column("price")
    stmt = select(price_column)

    filters = [
        price_column >= 100,
    ]

    result = BaseService.apply_filters(
        stmt,
        filters=filters,
    )

    sql = str(result)

    assert "WHERE" in sql


def test_apply_filters_with_multiple_filters():
    price_column = column("price")
    stock_column = column("stock")

    stmt = select(
        price_column,
        stock_column,
    )

    filters = [
        price_column >= 100,
        stock_column > 0,
    ]

    result = BaseService.apply_filters(
        stmt,
        filters=filters,
    )

    sql = str(result)

    assert "WHERE" in sql
    assert "price" in sql
    assert "stock" in sql
