-- Table 1: Provider Mappings
CREATE TABLE IF NOT EXISTS provider_mappings (
    provider_group_id BIGINT,
    npi BIGINT,
    tin_type VARCHAR(20),
    tin_value VARCHAR(50)
);

CREATE INDEX IF NOT EXISTS idx_provider_mappings_npi ON provider_mappings(npi);
CREATE INDEX IF NOT EXISTS idx_provider_mappings_group ON provider_mappings(provider_group_id);

-- Table 2: Billing Codes Reference
CREATE TABLE IF NOT EXISTS billing_codes (
    billing_code_type VARCHAR(50),
    billing_code VARCHAR(100) PRIMARY KEY,
    name TEXT,
    description TEXT
);

-- Table 3: Negotiated Rates
CREATE TABLE IF NOT EXISTS negotiated_rates (
    provider_group_id BIGINT,
    plan_name TEXT,
    billing_code_type VARCHAR(50),
    billing_code VARCHAR(100),
    negotiation_arrangement VARCHAR(50),
    negotiated_type VARCHAR(50),
    negotiated_rate NUMERIC(12, 2),
    expiration_date DATE,
    service_code TEXT
);

CREATE INDEX IF NOT EXISTS idx_rates_group ON negotiated_rates(provider_group_id);
CREATE INDEX IF NOT EXISTS idx_rates_code ON negotiated_rates(billing_code);

-- Table 4: Place of Service Codes (CMS Standard)
CREATE TABLE IF NOT EXISTS place_of_service_codes (
    service_code VARCHAR(10) PRIMARY KEY,
    name VARCHAR(255),
    description TEXT
);

-- Seed CMS Place of Service Codes (complete list)
INSERT INTO place_of_service_codes (service_code, name, description) VALUES
('01', 'Pharmacy', 'A facility or location where drugs and other medically related items and services are sold, dispensed, or otherwise provided directly to patients.'),
('02', 'Telehealth - Other', 'The location where health services and health related services are provided or received, through telecommunication technology from a distant site.'),
('03', 'School', 'A facility whose primary purpose is education.'),
('04', 'Homeless Shelter', 'A facility or location whose primary purpose is to provide temporary housing to homeless individuals.'),
('05', 'Indian Health Service Free-standing', 'A facility or location, owned and operated by the Indian Health Service, which provides diagnostic, therapeutic, and rehabilitation services.'),
('06', 'Indian Health Service Provider-based', 'A facility or location, owned and operated by the Indian Health Service, which provides diagnostic, therapeutic, and rehabilitation services rendered by, or under the supervision of, physicians.'),
('07', 'Tribal 638 Free-standing', 'A facility or location owned and operated by a federally recognized American Indian or Alaska Native tribe or tribal organization under a 638 agreement.'),
('08', 'Tribal 638 Provider-based', 'A facility or location owned and operated by a federally recognized American Indian or Alaska Native tribe or tribal organization under a 638 agreement that provides diagnostic, therapeutic, and rehabilitation services rendered by, or under the supervision of, physicians.'),
('09', 'Prison/Correctional Facility', 'A prison, jail, reformatory, work farm, detention center, or any other similar facility maintained by either Federal, State or local authorities for the purpose of confinement or rehabilitation of adult or juvenile criminal offenders.'),
('10', 'Telehealth - Patient Home', 'The location where health services and health related services are provided or received, through telecommunication technology in the patient home.'),
('11', 'Office', 'Location, other than a hospital, skilled nursing facility (SNF), military treatment facility, community health center, State or local public health clinic, or intermediate care facility (ICF), where the health professional routinely provides health examinations, diagnosis, and treatment of illness or injury on an ambulatory basis.'),
('12', 'Home', 'Location, other than a hospital or other facility, where the patient receives care in a private residence.'),
('13', 'Assisted Living Facility', 'Congregate residential facility with self-contained living units providing assessment of each residents needs and on-site support 24 hours a day, 7 days a week.'),
('14', 'Group Home', 'A residence, with shared living areas, where clients receive supervision and other services such as social and/or behavioral services, custodial service, and minimal services.'),
('15', 'Mobile Unit', 'A facility/unit that moves from place-to-place equipped to provide preventive, screening, diagnostic, and/or treatment services.'),
('16', 'Temporary Lodging', 'A short-term accommodation such as a hotel, campground, hostel, cruise ship or resort where the patient receives care.'),
('17', 'Walk-in Retail Health Clinic', 'A walk-in health clinic, other than an office, urgent care facility, pharmacy, or independent clinic and not described by any other Place of Service code.'),
('18', 'Place of Employment/Worksite', 'A location, not described by any other POS code, owned or operated by a public or private entity where the patient is employed.'),
('19', 'Off Campus-Outpatient Hospital', 'A portion of an off-campus hospital provider-based department which provides diagnostic, therapeutic, and rehabilitation services to sick or injured persons.'),
('20', 'Urgent Care Facility', 'Location, distinct from a hospital emergency room, an office, or a clinic, whose purpose is to diagnose and treat illness or injury for unscheduled, ambulatory patients seeking immediate medical attention.'),
('21', 'Inpatient Hospital', 'A facility, other than psychiatric, which primarily provides diagnostic, therapeutic (both surgical and nonsurgical), and rehabilitation services by, or under, the supervision of physicians to patients admitted for a variety of medical conditions.'),
('22', 'On Campus-Outpatient Hospital', 'A portion of a hospitals main campus which provides diagnostic, therapeutic (both surgical and nonsurgical), and rehabilitation services to sick or injured persons who do not require hospitalization or institutionalization.'),
('23', 'Emergency Room - Hospital', 'A portion of a hospital where emergency diagnosis and treatment of illness or injury is provided.'),
('24', 'Ambulatory Surgical Center', 'A freestanding facility, other than a physicians office, where surgical and diagnostic services are provided on an ambulatory basis.'),
('25', 'Birthing Center', 'A facility, other than a hospital maternity facilities or a physicians office, which provides a setting for labor, delivery, and immediate postpartum care as well as immediate care of newborn infants.'),
('26', 'Military Treatment Facility', 'A medical facility operated by one or more of the Uniformed Services.'),
('31', 'Skilled Nursing Facility', 'A facility which primarily provides inpatient skilled nursing care and related services to patients who require medical, nursing, or rehabilitative services but does not provide the level of care or treatment available in a hospital.'),
('32', 'Nursing Facility', 'A facility which primarily provides to residents skilled nursing care and related services for the rehabilitation of injured, disabled, or sick persons, or, on a regular basis, health-related care services above the level of custodial care to individuals other than those with intellectual disabilities.'),
('33', 'Custodial Care Facility', 'A facility which provides room, board and other personal assistance services, generally on a long-term basis.'),
('34', 'Hospice', 'A facility, other than a patients home, in which palliative and supportive care for terminally ill patients and their families are provided.'),
('41', 'Ambulance - Land', 'A land vehicle specifically designed, equipped and staffed for lifesaving and transporting the sick or injured.'),
('42', 'Ambulance - Air or Water', 'An air or water vehicle specifically designed, equipped and staffed for lifesaving and transporting the sick or injured.'),
('49', 'Independent Clinic', 'A location, not part of a hospital and not described by any other Place of Service code, that is organized and operated to provide preventive, diagnostic, therapeutic, rehabilitative, or palliative services to outpatients only.'),
('50', 'Federally Qualified Health Center', 'A facility located in a medically underserved area that provides Medicare beneficiaries preventive primary medical care under the general direction of a physician.'),
('51', 'Inpatient Psychiatric Facility', 'A facility that provides inpatient psychiatric services for the diagnosis and treatment of mental illness on a 24-hour basis, by or under the supervision of a physician.'),
('52', 'Psychiatric Facility - Partial Hospitalization', 'A facility for the diagnosis and treatment of mental illness that provides a planned therapeutic program for patients who do not require full time hospitalization.'),
('53', 'Community Mental Health Center', 'A facility that provides the following services: outpatient services, including specialized outpatient services for children, the elderly, individuals who are chronically ill, and residents of its mental health services area who have been discharged from inpatient treatment.'),
('54', 'Intermediate Care Facility/Individuals with Intellectual Disabilities', 'A facility which primarily provides health-related care and services above the level of custodial care to individuals with intellectual disabilities.'),
('55', 'Residential Substance Abuse Treatment Facility', 'A facility which provides treatment for substance (alcohol and drug) abuse to live-in residents who do not require the level of care provided by a hospital-based inpatient substance abuse treatment facility.'),
('56', 'Psychiatric Residential Treatment Center', 'A facility or distinct part of a facility for psychiatric care which provides a total 24-hour therapeutically planned and professionally staffed group living and learning environment.'),
('57', 'Non-residential Substance Abuse Treatment Facility', 'A location which provides treatment for substance (alcohol and drug) abuse on an ambulatory basis. Services include individual and group therapy and counseling, family counseling, laboratory tests, drugs and supplies, and psychological testing.'),
('58', 'Non-residential Opioid Treatment Facility', 'A location that provides treatment for opioid use disorder on an ambulatory basis, including dispensing and administration of FDA-approved medications for opioid use disorder.'),
('60', 'Mass Immunization Center', 'A location where providers administer pneumococcal pneumonia and influenza virus vaccinations and submit these claims as electronic media claims.'),
('61', 'Comprehensive Inpatient Rehabilitation Facility', 'A facility that provides comprehensive rehabilitation services under the supervision of a physician to inpatients with physical disabilities.'),
('62', 'Comprehensive Outpatient Rehabilitation Facility', 'A facility that provides comprehensive rehabilitation services under the supervision of a physician to outpatients with physical disabilities.'),
('65', 'End-Stage Renal Disease Treatment Facility', 'A facility other than a hospital, which provides dialysis treatment, maintenance, and/or training to patients or caregivers on an ambulatory or home-care basis.'),
('71', 'State or Local Public Health Clinic', 'A facility maintained by either State or local health departments that provides ambulatory primary medical care under the general direction of a physician.'),
('72', 'Rural Health Clinic', 'A certified facility which is located in a rural medically underserved area that provides ambulatory primary medical care under the general direction of a physician.'),
('81', 'Independent Laboratory', 'A laboratory certified to perform diagnostic and/or clinical tests independent of an institution or a physicians office.'),
('99', 'Other Place of Service', 'Other place of service not identified above.')
ON CONFLICT (service_code) DO NOTHING;

-- Table 5: Index Files (tracks all discovered rate file URLs for prioritized processing)
-- plan_names is an array because a single rate file is shared across many plans —
-- storing only the first plan name seen would silently drop individual market plans
-- that reference the same network file as employer groups.
CREATE TABLE IF NOT EXISTS index_files (
    id SERIAL PRIMARY KEY,
    reporting_entity_name TEXT,
    reporting_entity_type TEXT,
    plan_names TEXT[],
    description TEXT,
    location TEXT NOT NULL,
    file_size_bytes BIGINT,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    CONSTRAINT uq_index_files_location UNIQUE (location)
);

CREATE INDEX IF NOT EXISTS idx_index_files_plan ON index_files USING GIN(plan_names);
CREATE INDEX IF NOT EXISTS idx_index_files_status ON index_files(status);

-- Create View for Convenience
CREATE OR REPLACE VIEW vw_rates_detailed AS
SELECT
    r.provider_group_id,
    r.plan_name,
    r.billing_code_type,
    r.billing_code,
    b.name AS billing_code_name,
    b.description AS billing_code_description,
    r.negotiation_arrangement,
    r.negotiated_type,
    r.negotiated_rate,
    r.expiration_date,
    r.service_code,
    s.name AS service_code_name
FROM negotiated_rates r
LEFT JOIN billing_codes b ON r.billing_code = b.billing_code
LEFT JOIN place_of_service_codes s ON r.service_code = s.service_code;

-- Test Schema: mirrors production table structure for isolated ETL test runs.
-- Connected via search_path=test in the TEST_DATABASE_URL connection string.
-- Safe to TRUNCATE or DROP without affecting production data.
CREATE SCHEMA IF NOT EXISTS test;

CREATE TABLE IF NOT EXISTS test.provider_mappings      (LIKE public.provider_mappings      INCLUDING ALL);
CREATE TABLE IF NOT EXISTS test.billing_codes          (LIKE public.billing_codes          INCLUDING ALL);
CREATE TABLE IF NOT EXISTS test.negotiated_rates       (LIKE public.negotiated_rates       INCLUDING ALL);
CREATE TABLE IF NOT EXISTS test.place_of_service_codes (LIKE public.place_of_service_codes INCLUDING ALL);
CREATE TABLE IF NOT EXISTS test.index_files            (LIKE public.index_files            INCLUDING ALL);
