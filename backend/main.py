from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List, Optional
from sqlalchemy import func, text
from . import models, database
from pydantic import BaseModel

app = FastAPI(title="Honest Healthcare API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic formats
class MarketSummary(BaseModel):
    min_rate: float
    max_rate: float
    avg_rate: float

class RateResponse(BaseModel):
    facility_name: Optional[str] = None
    tin: Optional[str] = None
    network_name: Optional[str] = None
    billing_code: str
    billing_code_type: str
    procedure_name: Optional[str] = None
    payor: str
    plan_products: List[str]
    min_rate: float
    max_rate: float
    median_rate: float
    record_count: int

class SearchResponse(BaseModel):
    rates: List[RateResponse]
    market_summary: Optional[MarketSummary] = None

@app.get("/")
def read_root():
    return {"message": "Welcome to Honest Healthcare API (Anthem Clinical Source)"}

@app.get("/rates", response_model=SearchResponse)
def get_rates(
    code: Optional[str] = None, 
    search: Optional[str] = None,
    network: Optional[str] = None, 
    payor: Optional[str] = "anthem",
    plan: Optional[str] = None,
    test: bool = False,
    db: Session = Depends(database.get_db)
):
    table_name = "test_mrf_rates" if test else "mrf_rates"
    
    # Use raw SQL for the flexible table name while maintaining the same aggregation logic
    plan_product_expr = "COALESCE(split_part(plan_name, ' - ', 1), 'Standard Plan')"
    
    # Smart Display Name logic: Use business_name, or fallback to cleaned network_name
    display_name_sql = """
        CASE 
            WHEN (business_name IS NOT NULL AND business_name != '' AND business_name != 'Unknown Facility') THEN business_name
            WHEN (network_name IS NOT NULL AND network_name != '' AND network_name != '[]') THEN 
                TRIM(BOTH '[]''' FROM network_name)
            ELSE 'Unknown Provider'
        END
    """
    
    # 1. Main Query: Aggregated by Provider Group AND Price
    # We consolidate across different TINs if they are part of the same "Display Name" group
    sql = f"""
        SELECT 
            {display_name_sql} as display_name,
            string_agg(DISTINCT tin_value, ', ') as tins,
            MAX(network_name) as raw_network,
            billing_code, 
            billing_code_type, procedure_name, payor, 
            string_agg(DISTINCT {plan_product_expr}, ', ') as plan_products,
            MIN(negotiated_rate) as min_rate,
            MAX(negotiated_rate) as max_rate,
            AVG(negotiated_rate) as median_rate,
            COUNT(id) as record_count
        FROM {table_name}
        WHERE 1=1
    """
    params = {}
    if code:
        sql += " AND billing_code = :code"
        params["code"] = code
    if search:
        sql += " AND procedure_name ILIKE :search"
        params["search"] = f"%{search}%"
    if network:
        sql += " AND network_name = :network"
        params["network"] = network
    if payor:
        sql += " AND payor = :payor"
        params["payor"] = payor
    if plan:
        sql += f" AND {plan_product_expr} = :plan"
        params["plan"] = plan
        
    sql += f" GROUP BY display_name, billing_code, billing_code_type, procedure_name, payor"
    sql += " LIMIT 100"
    
    try:
        results = db.execute(text(sql), params).fetchall()
    except Exception as e:
        # If test table doesn't exist yet
        if "does not exist" in str(e):
            return {"rates": [], "market_summary": None}
        raise e
    
    rates = []
    for r in results:
        # Clean up stringified list artifacts from network
        network = r.raw_network
        if network and network.startswith("['") and network.endswith("']"):
            network = network[2:-2]
            
        # Handle plans
        plan_list = [p.strip() for p in r.plan_products.split(",")] if r.plan_products else ["Standard Plan"]
        
        # Handle multiple TINs
        tins = r.tins.split(", ") if r.tins else []
        display_tin = tins[0] if len(tins) == 1 else f"Multiple ({len(tins)})"
            
        rates.append({
            "facility_name": r.display_name,
            "tin": display_tin,
            "network_name": network or "Standard Network",
            "billing_code": r.billing_code,
            "billing_code_type": r.billing_code_type,
            "procedure_name": r.procedure_name,
            "payor": r.payor,
            "plan_products": plan_list,
            "min_rate": float(r.min_rate or 0),
            "max_rate": float(r.max_rate or 0),
            "median_rate": float(r.median_rate or 0),
            "record_count": int(r.record_count or 0)
        })

    # 2. Market Summary Query
    market_summary = None
    if code:
        summary_sql = f"SELECT MIN(negotiated_rate), MAX(negotiated_rate), AVG(negotiated_rate) FROM {table_name} WHERE billing_code = :code"
        stats = db.execute(text(summary_sql), {"code": code}).fetchone()
        
        if stats and stats[0] is not None:
            market_summary = {
                "min_rate": float(stats[0]),
                "max_rate": float(stats[1]),
                "avg_rate": float(stats[2])
            }

    return {"rates": rates, "market_summary": market_summary}

@app.get("/hospitals")
def get_networks(db: Session = Depends(database.get_db)):
    results = db.query(models.MRFRate.network_name).distinct().order_by(models.MRFRate.network_name).all()
    return [r[0] for r in results if r[0]]

@app.get("/payers")
def get_payers(db: Session = Depends(database.get_db)):
    results = db.query(models.MRFRate.payor).distinct().order_by(models.MRFRate.payor).all()
    return [r[0] for r in results]

@app.get("/plans")
def get_plans(payer: Optional[str] = None, db: Session = Depends(database.get_db)):
    plan_product_expr = func.split_part(models.MRFRate.plan_name, ' - ', 1)
    query = db.query(plan_product_expr).distinct()
    if payer:
        query = query.filter(models.MRFRate.payor == payer)
    results = query.order_by(plan_product_expr).all()
    return [r[0] for r in results]

@app.get("/procedures")
def get_procedures(
    search: Optional[str] = None,
    plan: Optional[str] = None,
    test: bool = False,
    db: Session = Depends(database.get_db)
):
    table_name = "test_mrf_rates" if test else "mrf_rates"
    sql = f"SELECT DISTINCT procedure_name FROM {table_name} WHERE 1=1"
    params = {}
    if search:
        sql += " AND procedure_name ILIKE :search"
        params["search"] = f"%{search}%"
    if plan:
        sql += " AND split_part(plan_name, ' - ', 1) = :plan"
        params["plan"] = plan
        
    sql += " ORDER BY procedure_name LIMIT 20"
    
    try:
        results = db.execute(text(sql), params).fetchall()
        return [r[0] for r in results if r[0]]
    except:
        return []
