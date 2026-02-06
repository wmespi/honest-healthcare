from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from . import models, database
from pydantic import BaseModel

app = FastAPI(title="Honest Healthcare API")

# Pydantic schemas
class RateResponse(BaseModel):
    hospital_name: str
    billing_code: str
    procedure_type: str
    level: str
    payer: str
    plan: str
    min_rate: float
    max_rate: float
    median_rate: float
    record_count: int

    class Config:
        from_attributes = True

@app.get("/")
def read_root():
    return {"message": "Welcome to Honest Healthcare API"}

@app.get("/rates", response_model=List[RateResponse])
def get_rates(
    code: Optional[str] = None, 
    hospital: Optional[str] = None, 
    db: Session = Depends(database.get_db)
):
    query = db.query(models.NegotiatedRate)
    
    if code:
        query = query.filter(models.NegotiatedRate.billing_code == code)
    if hospital:
        query = query.filter(models.NegotiatedRate.hospital_name == hospital)
        
    return query.limit(100).all()

@app.get("/hospitals")
def get_hospitals(db: Session = Depends(database.get_db)):
    results = db.query(models.NegotiatedRate.hospital_name).distinct().all()
    return [r[0] for r in results]
