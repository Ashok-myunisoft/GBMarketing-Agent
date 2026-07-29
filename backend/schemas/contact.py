from typing import Optional

from pydantic import BaseModel


class Contact(BaseModel):

    name: Optional[str] = None

    designation: Optional[str] = None

    email: Optional[str] = None

    phone: Optional[str] = None

    linkedin: Optional[str] = None
    