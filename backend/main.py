from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List, Optional
from . import models, database
from pydantic import BaseModel

app = FastAPI(title="Honest Healthcare API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For dev, we can allow all. For prod, we'd specify localhost:3000
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic schemas
class RateResponse(BaseModel):
    hospital_name: str
    billing_code: str
    billing_code_type: str
    procedure_type: str
    setting: str
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
    search: Optional[str] = None,
    hospital: Optional[str] = None, 
    setting: Optional[str] = None,
    payer: Optional[str] = None,
    plan: Optional[str] = None,
    db: Session = Depends(database.get_db)
):
    query = db.query(models.NegotiatedRate)
    
    if code:
        query = query.filter(models.NegotiatedRate.billing_code == code)
    if search:
        query = query.filter(models.NegotiatedRate.procedure_type.ilike(f"%{search}%"))
    if hospital:
        query = query.filter(models.NegotiatedRate.hospital_name == hospital)
    if setting:
        query = query.filter(models.NegotiatedRate.setting == setting.lower())
    if payer:
        query = query.filter(models.NegotiatedRate.payer == payer)
    if plan:
        query = query.filter(models.NegotiatedRate.plan == plan)
        
    return query.limit(400).all() # Increased limit for better comparisons

@app.get("/hospitals")
def get_hospitals(db: Session = Depends(database.get_db)):
    results = db.query(models.NegotiatedRate.hospital_name).distinct().order_by(models.NegotiatedRate.hospital_name).all()
    return [r[0] for r in results]

@app.get("/payers")
def get_payers(db: Session = Depends(database.get_db)):
    results = db.query(models.NegotiatedRate.payer).distinct().order_by(models.NegotiatedRate.payer).all()
    return [r[0] for r in results]

@app.get("/plans")
def get_plans(payer: Optional[str] = None, db: Session = Depends(database.get_db)):
    query = db.query(models.NegotiatedRate.plan).distinct()
    if payer:
        query = query.filter(models.NegotiatedRate.payer == payer)
    results = query.order_by(models.NegotiatedRate.plan).all()
    return [r[0] for r in results]

@app.get("/procedures")
def get_procedures(
    search: Optional[str] = None,
    hospital: Optional[str] = None,
    setting: Optional[str] = None,
    payer: Optional[str] = None,
    plan: Optional[str] = None,
    db: Session = Depends(database.get_db)
):
    query = db.query(models.NegotiatedRate.procedure_type).distinct()
    
    if search:
        query = query.filter(models.NegotiatedRate.procedure_type.ilike(f"%{search}%"))
    if hospital:
        query = query.filter(models.NegotiatedRate.hospital_name == hospital)
    if setting:
        query = query.filter(models.NegotiatedRate.setting == setting.lower())
    if payer:
        query = query.filter(models.NegotiatedRate.payer == payer)
    if plan:
        query = query.filter(models.NegotiatedRate.plan == plan)
        
    results = query.order_by(models.NegotiatedRate.procedure_type).limit(10).all()
    return [r[0] for r in results]
