from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ProductDB(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    price: Mapped[float] = mapped_column(Float)
    stock: Mapped[int] = mapped_column(Integer, default=0)
