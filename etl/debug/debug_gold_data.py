# %%
import pandas as pd

# %%
check = pd.read_csv('/app/data/gold/emory_gold.csv')

# %%
check.hospital_name.unique()

# %%
check.describe(include='all')

# %%
check[check.hospital_name == 'Emory University Hospital'].describe(include='all')

# %%
check.describe(include='all')
inpatient = check[check['setting'] == 'inpatient']
outpatient = check[check['setting'] == 'outpatient']

# %%
inpatient.describe(include='all')

# %%
inpatient[inpatient.median_rate > 2000000]

# %%
outpatient[outpatient.procedure_type == 'Airway Endoscopy Level 3'][
    ['hospital_name', 'payer', 'plan']
].drop_duplicates().sort_values(['hospital_name', 'payer', 'plan'])

# %%
outpatient[outpatient.procedure_type == 'CT and CTA without Contrast Composite'].hospital_name.unique()

# %%
outpatient.describe(include='all')

# %%
inpatient['plan'].unique()
