"""What can be bought with Hubtel besides access time.

Access itself ("access") is priced by the Settings -> Access price fields and
cut as an AccessCode (app.payments.service). Everything here is an AI
Generation extra, applied straight to the account: credit packs add runs that
are spent only after the day's allowance is used up, and the day pass lifts
the daily limit for 24 hours. Prices are admin-editable settings (0 hides a
product); the quantities are fixed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ACCESS = "access"


@dataclass(frozen=True)
class Product:
    key: str
    label: str
    kind: Literal["ai_credits", "ai_unlimited"]
    price_setting: str
    credits: int = 0
    hours: int = 0


AI_PRODUCTS: tuple[Product, ...] = (
    Product("ai_pack_3", "3 extra AI Generation runs", "ai_credits", "ai_pack_3_price", credits=3),
    Product("ai_pack_6", "6 extra AI Generation runs", "ai_credits", "ai_pack_6_price", credits=6),
    Product("ai_pack_10", "10 extra AI Generation runs", "ai_credits", "ai_pack_10_price", credits=10),
    Product("ai_unlimited_day", "Unlimited AI Generation for 24 hours", "ai_unlimited", "ai_unlimited_day_price", hours=24),
)

BY_KEY: dict[str, Product] = {p.key: p for p in AI_PRODUCTS}
