from sqlalchemy import Column, Integer, String, Float
from .database import Base

class NegotiatedRate(Base):
    __tablename__ = "emory_negotiated_rates"
    
    # Define primary key since to_sql doesn't create one by default
    id = Column(Integer, primary_key=True, index=True)
    hospital_name = Column(String, index=True)
    billing_code = Column(String, index=True)
    procedure_type = Column(String)
    level = Column(String)
    payer = Column(String)
    plan = Column(String)
    min_rate = Column(Float)
    max_rate = Column(Float)
    median_rate = Column(Float)
    record_count = Column(Integer)
