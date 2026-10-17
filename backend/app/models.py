"""Importa todos los modelos para que `Base.metadata` (y `create_all`) vea las 15 tablas de 01-data-model."""

from app.core.db import Base
from app.core.events import Event
from app.modules.loans.models import Loan, LoanPctChange
from app.modules.marketplace.models import LoanOffer, LoanRequest
from app.modules.notifications.models import Notification
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales.models import Payout, Repayment, Sale, WebhookEvent
from app.modules.scoring.models import CreditAssessment
from app.modules.users.models import User
from app.modules.waitlist.models import WaitlistEntry
from app.modules.worker.models import OpCursor

__all__ = [
    "Base",
    "CollectionAccount",
    "CreditAssessment",
    "Event",
    "Loan",
    "LoanOffer",
    "LoanPctChange",
    "LoanRequest",
    "Notification",
    "OpCursor",
    "Payout",
    "Repayment",
    "Sale",
    "User",
    "WaitlistEntry",
    "WebhookEvent",
]
