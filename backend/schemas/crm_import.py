from pydantic import BaseModel, ConfigDict, Field


class CRMImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_index: int = Field(ge=0)
    approved: bool = True
    company: str = Field(default="Goodbooks", max_length=300)
    lead_name: str = Field(min_length=1, max_length=500)
    description: str = Field(default="None", max_length=2000)
    contact_name: str = Field(default="None", max_length=300)
    designation: str = Field(default="None", max_length=300)
    contact_mobile_no: str = Field(default="None", max_length=100)
    contact_email: str = Field(default="None", max_length=500)
    address1: str = Field(default="None", max_length=2000)
    gst: str = Field(default="None", max_length=100)
    city: str = Field(default="None", max_length=300)
    stage: str = Field(default="Data", max_length=300)
    remarks: str = Field(default="None", max_length=2000)
    incharge: str = Field(default="None", max_length=300)
    alloted_to: str = Field(default="None", max_length=300)
    source_detail: str = Field(default="AI Agent", max_length=500)


class CRMImportRowsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[CRMImportRow] = Field(min_length=1, max_length=1000)
