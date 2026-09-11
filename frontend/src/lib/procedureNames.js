// Expands CMS/AMA abbreviations and title-cases procedure names for layman readability.
const ABBR = {
  OUTPT: 'Outpatient', INPT: 'Inpatient', PATIEN: 'Patient',
  ESTAB: 'Established', OFC: 'Office', HOSP: 'Hospital', SURG: 'Surgery',
  PROC: 'Procedure', EMER: 'Emergency', PREV: 'Preventive',
  SUBSEQ: 'Subsequent', INIT: 'Initial', MGT: 'Management',
  SVC: 'Service', SVCS: 'Services', ADMISS: 'Admission',
  PSYCH: 'Psychiatric', BEHAV: 'Behavioral', 'W/': 'with', 'W/O': 'without',
  NEC: 'Not Elsewhere Classified', NOS: 'Not Otherwise Specified',
  DX: 'Diagnosis', TX: 'Treatment', HX: 'History',
};

export function cleanProcedureName(name) {
  if (!name) return name;
  return name.split(';').map(part =>
    part.trim().split(/\s+/).map(word => {
      const up = word.toUpperCase();
      if (ABBR[up]) return ABBR[up];
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    }).join(' ')
  ).join(' — ');
}
