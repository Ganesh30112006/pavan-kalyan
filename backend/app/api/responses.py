"""Response helpers and query-parameter parsing shared by the routers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.errors import FieldError, invalid

MAX_PAGE_SIZE = 100
MAX_PAGE = 100_000
# The limits are enforced by check_paging (with friendlier messages); these only publish them in the schema.
PageQuery = Annotated[int, Query(json_schema_extra={"minimum": 1, "maximum": MAX_PAGE})]
PageSizeQuery = Annotated[int, Query(json_schema_extra={"minimum": 1, "maximum": MAX_PAGE_SIZE})]


def respond(model: BaseModel | list[BaseModel] | dict[str, Any], status: int = 200) -> JSONResponse:
    if isinstance(model, BaseModel):
        content: Any = model.model_dump(mode="json")
    elif isinstance(model, list):
        content = [m.model_dump(mode="json") for m in model]
    else:
        content = model
    return JSONResponse(content=content, status_code=status)


def check_paging(page: int, page_size: int) -> None:
    errors = []
    if page < 1 or page > MAX_PAGE:
        errors.append(FieldError("page", "Page must be 1 or more."))
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        errors.append(FieldError("page_size", f"Page size must be between 1 and {MAX_PAGE_SIZE}."))
    if errors:
        raise invalid(errors, "The page parameters are not valid.")


def paginate(db: Session, query: Select[Any], page: int, page_size: int) -> tuple[Sequence[Any], int]:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return rows, int(total)


def parse_list(value: str | None, allowed: Sequence[str], field: str, *, lower: bool = False) -> list[str]:
    """Comma-separated filter values (`status=failed,failed_rollback`). Unknown values are a 422."""
    if not value:
        return []
    items = [v.strip().lower() if lower else v.strip() for v in value.split(",") if v.strip()]
    unknown = [v for v in items if v not in allowed]
    if unknown:
        raise invalid([FieldError(field, f"Unknown value: {', '.join(unknown[:5])}.")], "A filter value is not valid.")
    return items


def parse_sort(value: str | None, allowed: Sequence[str]) -> tuple[str, bool] | None:
    """`name` ascending, `-name` descending."""
    if not value:
        return None
    descending = value.startswith("-")
    key = value[1:] if descending else value
    if key not in allowed:
        raise invalid(
            [FieldError("sort", f"Sort by one of: {', '.join(allowed)}.")], "The sort parameter is not valid."
        )
    return key, descending
