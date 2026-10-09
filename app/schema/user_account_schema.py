#!/usr/bin/env python3

from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, computed_field

from app.model.mod_enum import CurrencyType
from app.services.storage.signed_urls import links_expire_at, sign_key

class AccountDBSchema(BaseModel):
    """
    This schema validates user information before account creation. The
    router builds account_document from the already-uploaded file's object
    key (see UploadedFile elsewhere) - never raw file bytes, and never a
    value a client could set directly.
    """
    user_id:str = Field(description="User Id")
    bank_name:str = Field(description="Bank Name")
    account_number:str = Field(description="User saving account number")
    balance:Decimal = Field(default=Decimal("0"), description="User current balance")
    currency:CurrencyType = Field(description="User account currency")
    account_document_id:str = Field(description="Reference number printed on the proof document")
    account_document:str = Field(description="Object key of the uploaded proof-of-account document")

class AccountUpdate(BaseModel):
    """
    Partial update of an existing UserAccount. Only the uploaded proof
    document can be changed - bank_name and account_number together identify
    the real account (that's the unique constraint), currency is fixed by the
    real account, and balance must never be settable directly by the client:
    it has to come from an actual bank sync or transaction record, not a
    number the user types in.

    account_document, like on AccountDBSchema, is the already-uploaded file's
    object key - the router sets it after uploading, never the client directly.
    """
    account_document_id: Optional[str] = None
    account_document: Optional[str] = None

class UserAccountResponse(BaseModel):
    """
    Response shape for a UserAccount record. The bucket is private, so
    account_document is a temporary signed link, made fresh each time this
    response is built and valid until links_expire_at - never the raw object key.
    """
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    bank_name: str
    account_number: str
    balance: Decimal
    currency: CurrencyType
    account_document_id: Optional[str] = None
    created_at: datetime

    account_document: str = Field(exclude=True)   # the stored object key - never shown raw

    @computed_field  # type: ignore[prop-decorator]
    @property
    def account_document_link(self) -> str:
        return sign_key(self.account_document)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def links_expire_at(self) -> datetime:
        return links_expire_at()
