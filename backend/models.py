from sqlalchemy import Column, Integer, String, Float
from .database import Base

class NegotiatedRate(Base):
    __tablename__ = "emory_negotiated_rates"
    
    # Define primary key since to_sql doesn't create one by default
    id = Column(Integer, primary_key=True, index=True)
    hospital_name = Column(String, index=True)
    billing_code = Column(String, index=True)
    billing_code_type = Column(String)
    procedure_type = Column(String)
    setting = Column(String, index=True)
    payer = Column(String)
    plan = Column(String)
    min_rate = Column(Float)
    max_rate = Column(Float)
    median_rate = Column(Float)
    record_count = Column(Integer)

class MRFRate(Base):
    __tablename__ = "mrf_rates"
    
    id = Column(Integer, primary_key=True, index=True)
    payor = Column(String, index=True)  # e.g. "anthem"
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
    source_file = Column(String)
