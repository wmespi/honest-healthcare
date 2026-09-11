// Light title-casing for the ALL-CAPS NPPES org names, keeping entity suffixes upper.
const ORG_SUFFIX = new Set(['LLC', 'INC', 'PC', 'PA', 'LLP', 'LP', 'MD', 'DO', 'DDS', 'CORP', 'CO']);

export function titleCaseOrg(name) {
  if (!name) return name;
  return name.split(/\s+/).map(w => {
    const bare = w.replace(/[.,]/g, '').toUpperCase();
    if (ORG_SUFFIX.has(bare)) return w.toUpperCase();
    return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
  }).join(' ');
}

// Name for one billing practice — the group's tin_value resolved to an org
// name, else a member org / physician name, else the taxonomy, else the raw id.
export function providerLabel(r) {
  if (r.practice_name) return titleCaseOrg(r.practice_name);
  const orgs = (r.ga_org_names || []).filter(Boolean);
  if (orgs.length) return titleCaseOrg(orgs[0]);
  const indiv = (r.ga_indiv_names || []).filter(Boolean);
  if (indiv.length) return titleCaseOrg(indiv[0]);
  const tax = (r.ga_taxonomies || []).find(t => t && t !== 'Other');
  if (tax) return tax;
  return `Practice ${r.practice_id}`;
}
