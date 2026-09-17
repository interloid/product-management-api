from enum import StrEnum


class PaginationEnum:
    DEFAULT_PAGE = 1
    DEFAULT_PAGE_SIZE = 10
    MAX_PAGE_SIZE = 100


class ProductStatusEnum(StrEnum):
    ACTIVE = "active"
    DRAFT = "draft"
    OUT_OF_STOCK = "out_of_stock"
    ARCHIVED = "archived"


class OAuthProviderEnum(StrEnum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    GITHUB = "github"


class ProductImageConstants:
    MAX_IMAGES = 6
    MAX_FILE_SIZE = 5 * 1024 * 1024

    ALLOWED_CONTENT_TYPES = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    EXTENSION_BY_CONTENT_TYPE = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }


class RoleEnum(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


class PermissionEnum(StrEnum):
    VIEW_PRODUCTS = "view_products"
    CREATE_PRODUCTS = "create_products"
    UPDATE_PRODUCTS = "update_products"
    MANAGE_IMAGES = "manage_images"
    DELETE_PRODUCTS = "delete_products"
    MANAGE_USERS = "manage_users"


SCOPES: dict[str, str] = {
    "products:read": "View products and categories",
    "products:write": "Create and update products",
    "images:write": "Upload and manage product images",
    "users:read": "View users",
    "users:write": "Manage users and roles",
    "admin": "Full administrative access",
}

ROLE_SCOPES: dict[RoleEnum, frozenset[str]] = {
    RoleEnum.VIEWER: frozenset({"product:read"}),
    RoleEnum.EDITOR: frozenset(
        {
            "products:read",
            "products:write",
            "images:write",
        }
    ),
    RoleEnum.ADMIN: frozenset(
        SCOPES.keys(),
    ),
}


ROLE_PERMISSIONS: dict[RoleEnum, frozenset[PermissionEnum]] = {
    RoleEnum.VIEWER: frozenset(
        {
            PermissionEnum.VIEW_PRODUCTS,
        }
    ),
    RoleEnum.EDITOR: frozenset(
        {
            PermissionEnum.VIEW_PRODUCTS,
            PermissionEnum.CREATE_PRODUCTS,
            PermissionEnum.UPDATE_PRODUCTS,
            PermissionEnum.MANAGE_IMAGES,
        }
    ),
    RoleEnum.ADMIN: frozenset(
        {
            PermissionEnum.VIEW_PRODUCTS,
            PermissionEnum.CREATE_PRODUCTS,
            PermissionEnum.UPDATE_PRODUCTS,
            PermissionEnum.MANAGE_IMAGES,
            PermissionEnum.DELETE_PRODUCTS,
            PermissionEnum.MANAGE_USERS,
        }
    ),
}
