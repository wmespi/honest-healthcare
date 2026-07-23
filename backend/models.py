from sqlalchemy import Column, Integer, String, Float
from .database import Base

class MRFRate(Base):
    """
    Standard model for MRF Negotiated Rates.
    The table name is 'mrf_rates' by default, but the pipeline can override this 
    at the database level for isolated testing.
    """
    __tablename__ = "mrf_rates"
    
    id = Column(Integer, primary_key=True, index=True)
    payor = Column(String, index=True)
    npi = Column(String, index=True)
    billing_code = Column(String, index=True)
    billing_code_type = Column(String, index=True)
    procedure_name = Column(String)
    negotiated_rate = Column(Float)
    negotiated_type = Column(String)
    billing_class = Column(String)
    service_codes = Column(String)
    expiration_date = Column(String)
    network_name = Column(String)
    plan_name = Column(String)
    business_name = Column(String, index=True)
    tin_value = Column(String)
    source_file = Column(String)
