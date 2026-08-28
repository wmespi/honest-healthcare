package main

// gaPriorityExpr is a SQL scalar (0–3) ranking an index_files row for the
// project's primary use case: individual-market Georgia rates (the plan
// "BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM").
//
// Every signal is deterministic and structural — no regex, no matching against
// free-text plan names:
//   - market_types contains 'individual'        (from reporting_plans.plan_market_type)
//   - plan_states contains 'GA'                  (HIOS plan_id[5:7], positional)
//   - hios_issuer_ids ∩ {49046,45334,44113}     (Anthem GA issuer IDs seen in the data)
//   - location is an anthem/GA_ plan-specific file on the antm S3 bucket
//
// NOTE: the anthembcbsga.mrf.bcbs.com host is the BlueCard *mirror* — it serves
// files for every Blues plan (Highmark, BCBS-MN, …), so host alone is NOT a GA
// signal and is deliberately excluded.
//
// Tiers: 3 = individual AND a strong GA signal; 2 = individual (any state);
// 1 = a strong GA signal (any market); 0 = everything else.
const gaStrongSignal = `(
       'GA' = ANY(plan_states)
    OR hios_issuer_ids && ARRAY['49046','45334','44113']
    OR location LIKE '%.amazonaws.com/anthem/GA_%'
)`

const gaPriorityExpr = `(
  CASE
    WHEN 'individual' = ANY(market_types) AND ` + gaStrongSignal + ` THEN 3
    WHEN 'individual' = ANY(market_types) THEN 2
    WHEN ` + gaStrongSignal + ` THEN 1
    ELSE 0
  END
)`
