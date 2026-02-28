# %% [markdown]
# # Anthem MRF Data Exploration
# This is a pure Python script that uses VS Code's "Interactive Window" (Cell Mode).
# Use `# %%` to define a cell. Run a cell with 'Shift+Enter' or the 'Run Cell' button.

# %%
import os
import pandas as pd
import sqlalchemy
from sqlalchemy import create_engine, text
import matplotlib.pyplot as plt

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/honest_healthcare")
engine = create_engine(DATABASE_URL)

def q(sql: str):
    """Utility to run SQL and return a DataFrame"""
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)

# %% [markdown]
# ### 1. Quick Data Overview
# Let's see the first few rows of our clinical data.

# %%
q("SELECT * FROM mrf_rates LIMIT 10")


# %%
top_procedures = q("""
    SELECT DISTINCT source_file
    FROM mrf_rates
    LIMIT 15
""")
top_procedures

# %% [markdown]
# ### 2. Health Check: Billing Code Types
# Are we actually seeing a mix of CPT and HCPCS?

# %%
q("""
    SELECT billing_code_type, COUNT(*) as count 
    FROM mrf_rates 
    GROUP BY billing_code_type 
    ORDER BY count DESC
""")

# %% [markdown]
# ### 3. Top Procedures by Record Count
# What are the most frequently reported clinical procedures?

# %%
top_procedures = q("""
    SELECT procedure_name, billing_code, COUNT(*) as count
    FROM mrf_rates
    WHERE procedure_name IS NOT NULL
    GROUP BY procedure_name, billing_code
    ORDER BY count DESC
    LIMIT 15
""")
top_procedures

# %% [markdown]
# ### 4. Rate Distribution for a Specific Code
# Example: Let's pick a common code (e.g. '99213' for Office Visit) and see the distribution.

# %%
sample_code = '99213'
rates = q(f"SELECT negotiated_rate FROM mrf_rates WHERE billing_code = '{sample_code}'")

if not rates.empty:
    rates['negotiated_rate'].hist(bins=50, figsize=(10,6))
    plt.title(f"Negotiated Rate Distribution for {sample_code}")
    plt.xlabel("Rate ($)")
    plt.ylabel("Frequency")
    plt.show()
else:
    print(f"No data found for code {sample_code}")

# %% [markdown]
# ### 5. Resolve Plan Diversity
# How many unique plans have we captured in this 5GB sample?

# %%
q("SELECT plan_name, COUNT(*) as record_count FROM mrf_rates GROUP BY plan_name ORDER BY record_count DESC LIMIT 20")
