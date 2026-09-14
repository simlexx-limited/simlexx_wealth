from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from firebase_admin import auth

from sqlalchemy import select, exists, or_
from sqlalchemy.ext.asyncio import AsyncSession

from functools import lru_cache

from app.model.models import Users

class Auth:
    def __init__(self, security:HTTPBearer, session:AsyncSession):
        self.__security = security
        self.__db_session = session

    @property
    def db_session(self) -> AsyncSession:
        return self.__db_session

    async def verify_credentials(self,
                                 credentials:HTTPAuthorizationCredentials = Depends(HTTPBearer())):
        """
        This function checks and validate JWT token from firebase
        """
        token = credentials.credentials
        try:
            decode_token = auth.verify_id_token(token)
            return decode_token
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not authorized to access this ressource",
                headers={"WWW-Authenticate":"Bearer"}
            )
    async def check_phone_exists(self, phone_number:str) -> bool:
        stment = select(exists().where(Users.phone_number == phone_number))
        result = await self.__db_session.execute(stment)
        return bool(result.scalar())

    async def check_email_exists(self, email:Optional[str] = None) -> bool:
        if not email:
            return False
        stment = select(exists().where(Users.email_address == email))
        result = await self.__db_session.execute(stment)
        return bool(result.scalar())

    async def verify_account_exist(self,
                                   phone_number:str,
                                   email:Optional[str]=None
                                   ) -> bool:
        """This function check for duplicate"""
        conditions = [Users.phone_number == phone_number]
        if email is not None:
            conditions.append(Users.email_address == email)

        stmnt = select(exists().where(or_(*conditions)))
        result = await self.__db_session.execute(stmnt)
        return bool(result.scalar())

@lru_cache
def authentication():
    security = HTTPBearer()
    return Auth(security=security, session=None)
