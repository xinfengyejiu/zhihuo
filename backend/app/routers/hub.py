"""产品选择台 API。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..products import list_products
from ..security import get_current_user

router = APIRouter(prefix="/hub", tags=["hub"])


class ProductOut(BaseModel):
    key: str
    name: str
    description: str
    href: str
    enabled: bool


class HubOut(BaseModel):
    products: list[ProductOut]
    last_product_key: str | None = None


class LastProductIn(BaseModel):
    product_key: str


@router.get("", response_model=HubOut)
def get_hub(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    products = [
        ProductOut(key=p.key, name=p.name, description=p.description, href=p.href, enabled=p.enabled)
        for p in list_products()
    ]
    return HubOut(products=products, last_product_key=user.last_product_key)


@router.put("/last-product")
def set_last_product(
    body: LastProductIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.last_product_key = body.product_key[:32]
    db.commit()
    return {"ok": True, "last_product_key": user.last_product_key}


@router.get("/registry")
def product_registry():
    """产品注册表:后续新产品在 products.PRODUCTS 登记即可出现在 Hub。"""
    from ..products import PRODUCTS

    return {
        "products": [
            {
                "key": p.key,
                "name": p.name,
                "description": p.description,
                "href": p.href,
                "enabled": p.enabled,
                "purposes": list(p.purposes),
            }
            for p in PRODUCTS.values()
        ]
    }
