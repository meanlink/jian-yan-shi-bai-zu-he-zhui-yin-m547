from pydantic import BaseModel, Field


class HandoverInput(BaseModel):
    batch_no: str = Field(min_length=2, max_length=80)
    from_library: str = Field(min_length=2, max_length=80)
    to_library: str = Field(min_length=2, max_length=80)
    sample_count: int = Field(ge=1, le=10000)
    receiver_id: int
    note: str = Field(default='', max_length=500)


class SignInput(BaseModel):
    received_count: int = Field(ge=0, le=10000)
    difference_note: str = Field(default='', max_length=500)


class WithdrawInput(BaseModel):
    reason: str = Field(default='', max_length=500)


class ResolveInput(BaseModel):
    resolution: str = Field(pattern='^(accept|return)$')
    reason: str = Field(min_length=5, max_length=1000)
