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
//   - location on the anthembcbsga host or an anthem/GA_ path prefix
//
// Tiers: 3 = individual AND a GA signal; 2 = individual (any state);
// 1 = a GA signal (any market); 0 = everything else.
const gaPriorityExpr = `(
  CASE
    WHEN 'individual' = ANY(market_types) AND (
           'GA' = ANY(plan_states)
        OR hios_issuer_ids && ARRAY['49046','45334','44113']
        OR location LIKE '%anthembcbsga.mrf.bcbs.com%'
        OR location LIKE '%/GA_%'
    ) THEN 3
    WHEN 'individual' = ANY(market_types) THEN 2
    WHEN 'GA' = ANY(plan_states)
      OR hios_issuer_ids && ARRAY['49046','45334','44113']
      OR location LIKE '%anthembcbsga.mrf.bcbs.com%'
      OR location LIKE '%/GA_%' THEN 1
    ELSE 0
  END
)`
