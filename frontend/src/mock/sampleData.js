// Fictional places, doctors and prices for the /mock/find-care experience. Nothing here
// comes from real data, and the names are made up so a sample price never sits next
// to a real business or person.

// priceFactor stands in for each plan negotiating its own rates.
export const PLANS = [
  { id: 'blue-value', name: 'Blue Value HMO', carrier: 'Anthem', needsReferral: true, priceFactor: 1 },
  { id: 'pathway', name: 'Pathway PCP Copay Choice', carrier: 'Anthem', needsReferral: false, priceFactor: 1.14 },
  { id: 'hartwell-silver', name: 'Hartwell Silver PPO', carrier: 'Sample Health', needsReferral: false, priceFactor: 0.88 },
  { id: 'hartwell-bronze', name: 'Hartwell Bronze HMO', carrier: 'Sample Health', needsReferral: true, priceFactor: 1.27 },
];

export const KIND_LABELS = {
  imaging: 'Imaging center',
  radiology: 'Radiology group',
  hospital: 'Hospital outpatient',
  ortho: 'Orthopedic practice',
  clinic: 'Medical practice',
};

export const SERVICES = [
  { id: 'mri-lower-back', name: 'MRI of the lower back', basePrice: 900, kinds: ['imaging', 'radiology', 'hospital'], keywords: 'lumbar spine scan' },
  { id: 'mri-knee', name: 'MRI of the knee', basePrice: 850, kinds: ['imaging', 'radiology', 'hospital', 'ortho'], keywords: 'joint scan' },
  { id: 'mri-brain', name: 'MRI of the brain', basePrice: 1100, kinds: ['imaging', 'radiology', 'hospital'], keywords: 'head scan' },
  { id: 'ct-abdomen', name: 'CT of the abdomen and pelvis', basePrice: 1000, kinds: ['imaging', 'radiology', 'hospital'], keywords: 'cat scan stomach' },
  { id: 'chest-xray', name: 'Chest X-ray', basePrice: 90, kinds: ['imaging', 'radiology', 'hospital', 'clinic'], keywords: 'xray lungs' },
  { id: 'knee-xray', name: 'X-ray of the knee', basePrice: 110, kinds: ['imaging', 'radiology', 'hospital', 'ortho'], keywords: 'xray joint' },
  { id: 'abdominal-ultrasound', name: 'Abdominal ultrasound', basePrice: 310, kinds: ['imaging', 'radiology', 'hospital'], keywords: 'sonogram stomach' },
  { id: 'mammogram', name: 'Screening mammogram', basePrice: 190, kinds: ['imaging', 'radiology', 'hospital'], keywords: 'breast' },
];

// The separate charges behind a full price.
export const SCAN_PARTS = [
  { label: 'The scan', share: 0.82 },
  { label: 'Radiologist’s reading', share: 0.18 },
];

// The bill a plan's file can't price at each kind of place, and what someone can do about
// it. A place with one of these has no "most you'd pay", only what's known so far.
export const MISSING_BILLS = {
  hospital: {
    label: 'Hospital facility fee',
    rowNote: 'Missing the hospital’s fee',
    explain: 'Hospitals bill their own fee for the scan, and your plan’s file doesn’t list it here. The doctors who read scans here are in your network, but nothing on file confirms the hospital itself is. Ask the hospital whether it’s in your network and what it would charge before you book.',
  },
  radiology: {
    label: 'The scan',
    rowNote: 'Missing the cost of the scan',
    explain: 'Medicare records show this group’s doctors reading scans done at hospitals, so the place that does your scan bills for it separately. Ask where the scan would happen and what that place charges.',
  },
};

const CONTRAST = 'Contrast dye, which makes it a different and pricier scan';
const OUTSIDE_NETWORK = 'Someone outside your network taking part, such as whoever reads the scan. Federal law limits most of these bills.';
const NOT_COVERED = 'Your plan not covering it, for example if a required approval was missed';
const ASK_APPROVAL = 'Does my plan need to approve this first?';
const ASK_ALL_IN = 'Is this everything, or will anyone else send me a separate bill?';
const ASK_NETWORK = 'Is everyone involved in my network?';
const ASK_EXACT = 'Is this the exact scan my doctor ordered?';
const SCAN_ASKS = [ASK_APPROVAL, ASK_ALL_IN, ASK_NETWORK, ASK_EXACT];

// What the screen says about each scan, in plain-language sample copy.
export const SERVICE_DETAILS = {
  'mri-lower-back': {
    about: 'Uses magnets and radio waves to picture your lower spine, with no radiation. It usually takes 30 to 60 minutes. Tell them about any metal implants.',
    couldAdd: [CONTRAST, OUTSIDE_NETWORK, NOT_COVERED], asks: SCAN_ASKS,
  },
  'mri-knee': {
    about: 'Uses magnets and radio waves to picture the bones, cartilage and ligaments in your knee, with no radiation. It usually takes 30 to 45 minutes. Tell them about any metal implants.',
    couldAdd: [CONTRAST, OUTSIDE_NETWORK, NOT_COVERED], asks: SCAN_ASKS,
  },
  'mri-brain': {
    about: 'Uses magnets and radio waves to picture your brain, with no radiation. It usually takes 30 to 60 minutes. Tell them about any metal implants.',
    couldAdd: [CONTRAST, OUTSIDE_NETWORK, NOT_COVERED], asks: SCAN_ASKS,
  },
  'ct-abdomen': {
    about: 'Combines X-rays from many angles into detailed pictures of your abdomen and pelvis. The scan itself usually takes a few minutes, though the visit runs longer.',
    couldAdd: [CONTRAST, OUTSIDE_NETWORK, NOT_COVERED], asks: SCAN_ASKS,
  },
  'chest-xray': {
    about: 'A quick, low-dose X-ray of your heart and lungs. It usually takes about 15 minutes.',
    couldAdd: [OUTSIDE_NETWORK, NOT_COVERED], asks: [ASK_ALL_IN, ASK_NETWORK, ASK_EXACT],
  },
  'knee-xray': {
    about: 'A quick, low-dose X-ray of the bones in your knee. It usually takes about 15 minutes.',
    couldAdd: [OUTSIDE_NETWORK, NOT_COVERED], asks: [ASK_ALL_IN, ASK_NETWORK, ASK_EXACT],
  },
  'abdominal-ultrasound': {
    about: 'Uses sound waves to picture the organs in your abdomen, with no radiation. It usually takes about 30 minutes, and you may be asked not to eat beforehand.',
    couldAdd: [OUTSIDE_NETWORK, NOT_COVERED], asks: SCAN_ASKS,
  },
  mammogram: {
    about: 'A low-dose X-ray that screens for breast cancer. It usually takes about 20 minutes.',
    preventive: 'Screening mammograms are usually $0 in network because they count as preventive care. The most you would pay matters if yours is billed as diagnostic.',
    couldAdd: ['Being billed as diagnostic rather than screening', OUTSIDE_NETWORK], asks: [ASK_ALL_IN, ASK_NETWORK, 'Will this be billed as a screening?'],
  },
};

const HOURS_BY_KIND = {
  imaging: 'Mon to Sat, 7am to 7pm',
  radiology: 'Mon to Fri, 8am to 5pm',
  hospital: 'Mon to Fri, 6am to 8pm',
  ortho: 'Mon to Fri, 8am to 5pm',
  clinic: 'Mon to Fri, 8am to 5pm',
};

const DOCTOR_COUNT_BY_KIND = { imaging: 3, radiology: 3, hospital: 7, ortho: 3, clinic: 2 };
const SPECIALTIES_BY_KIND = {
  imaging: ['Diagnostic radiology'],
  radiology: ['Diagnostic radiology', 'Neuroradiology'],
  hospital: ['Diagnostic radiology', 'Neuroradiology', 'Musculoskeletal radiology'],
  ortho: ['Orthopedic surgery', 'Sports medicine'],
  clinic: ['Family medicine', 'Internal medicine'],
};
const FIRST_NAMES = ['Maya', 'Daniel', 'Priya', 'Marcus', 'Elena', 'Samuel', 'Grace', 'Andre', 'Naomi', 'Victor', 'Leila', 'Owen', 'Tessa'];
const LAST_NAMES = ['Okafor', 'Lindqvist', 'Ramanathan', 'Whitfield', 'Castellano', 'Abernathy', 'Nakamura', 'Delacroix', 'Haddad', 'Prescott', 'Sandoval', 'Mbeki', 'Thornbury', 'Galloway', 'Iverson', 'Rosenthal', 'Beaumont'];

let doctorsMade = 0;

// score stands in for Medicare's clinician quality score, which some doctors don't have.
function makeDoctors(kind) {
  const specialties = SPECIALTIES_BY_KIND[kind];
  return Array.from({ length: DOCTOR_COUNT_BY_KIND[kind] }, (_, n) => {
    const i = doctorsMade++;
    return {
      name: `Dr. ${FIRST_NAMES[i % FIRST_NAMES.length]} ${LAST_NAMES[i % LAST_NAMES.length]}`,
      specialty: specialties[n % specialties.length],
      score: i % 5 === 4 ? null : 68 + ((i * 37) % 32),
    };
  });
}

// priceLevel scales a scan's basePrice, so a hospital's reading costs more than an imaging center's.
// spread is how far a place's lowest rate sits below its highest; most places have one rate.
// stars stands in for Medicare's hospital rating, and billers for who bills at this address.
const row = (id, name, kind, neighborhood, street, city, zip, lat, lon, phone, priceLevel, { spread = 0, stars = null, billers = [name] } = {}) => ({
  id, name, kind, neighborhood, address: `${street}, ${city}, GA ${zip}`, lat, lon, phone,
  hours: HOURS_BY_KIND[kind], priceLevel, spread, stars, billers, doctors: makeDoctors(kind),
});

// One entry per place someone would go to, so each office of a practice is its own place.
export const LOCATIONS = [
  row('img-dogwood', 'Dogwood Imaging', 'imaging', 'Decatur', '215 E Ponce de Leon Ave', 'Decatur', '30030', 33.7752, -84.2935, '(404) 555-0101', 0.42),
  row('img-dogwood-tucker', 'Dogwood Imaging', 'imaging', 'Tucker', '4200 Hugh Howell Rd', 'Tucker', '30084', 33.8445, -84.2055, '(770) 555-0141', 0.42),
  row('img-clearpath', 'Clearpath Radiology', 'imaging', 'Midtown', '950 Spring St NW', 'Atlanta', '30309', 33.7815, -84.3892, '(404) 555-0102', 0.58, { spread: 0.35, billers: ['Clearpath Radiology', 'Midtown Radiology Partners'] }),
  row('img-magnolia', 'Magnolia MRI & CT', 'imaging', 'Sandy Springs', '5670 Roswell Rd', 'Sandy Springs', '30328', 33.916, -84.3785, '(404) 555-0103', 0.5),
  row('img-beltline', 'Beltline Imaging', 'imaging', 'Inman Park', '310 Krog St NE', 'Atlanta', '30307', 33.7572, -84.364, '(404) 555-0104', 0.47),
  row('img-chattahoochee', 'Chattahoochee Diagnostic', 'imaging', 'Vinings', '3200 Paces Ferry Pl', 'Atlanta', '30339', 33.863, -84.469, '(770) 555-0105', 0.62, { spread: 0.25 }),
  row('img-redclay', 'Red Clay Imaging', 'imaging', 'East Point', '2760 Main St', 'East Point', '30344', 33.6785, -84.4395, '(404) 555-0106', 0.38),
  row('img-lostmtn', 'Lost Mountain Imaging', 'imaging', 'Kennesaw', '1810 Cobb Pkwy NW', 'Kennesaw', '30152', 34.02, -84.612, '(770) 555-0107', 0.44),
  row('img-corners', 'Corners Imaging', 'imaging', 'Norcross', '5525 Jimmy Carter Blvd', 'Norcross', '30093', 33.923, -84.208, '(770) 555-0108', 0.4),
  row('img-ashford', 'Ashford Open MRI', 'imaging', 'Dunwoody', '4700 Ashford Dunwoody Rd', 'Dunwoody', '30338', 33.933, -84.337, '(770) 555-0109', 0.66),
  row('img-harbor', 'Harbor Oak Radiology', 'imaging', 'Smyrna', '2520 Atlanta Rd SE', 'Smyrna', '30080', 33.866, -84.512, '(770) 555-0110', 0.52),
  row('img-oldmilton', 'Old Milton Imaging', 'imaging', 'Alpharetta', '3400 Old Milton Pkwy', 'Alpharetta', '30005', 34.071, -84.281, '(770) 555-0111', 0.55, { spread: 0.3, billers: ['Old Milton Imaging', 'North Fulton Radiology Group'] }),
  row('img-sugarloaf', 'Sugarloaf Scan Center', 'imaging', 'Duluth', '2100 Satellite Blvd', 'Duluth', '30097', 33.99, -84.133, '(770) 555-0112', 0.46),

  row('rad-peachtree', 'Peachtree Radiology Associates', 'radiology', 'Buckhead', '3200 Peachtree Rd NE', 'Atlanta', '30305', 33.848, -84.369, '(404) 555-0143', 0.5),
  row('rad-midtown', 'Midtown Radiology Partners', 'radiology', 'Midtown', '1100 Juniper St NE', 'Atlanta', '30309', 33.785, -84.383, '(404) 555-0144', 0.52),

  row('hos-ponce', 'Ponce Street Medical Center', 'hospital', 'Midtown', '550 Ponce de Leon Ave NE', 'Atlanta', '30308', 33.7725, -84.369, '(404) 555-0113', 1.55, { stars: 4, billers: ['Ponce Street Medical Center', 'Midtown Radiology Partners', 'Atlanta Diagnostic Physicians'] }),
  row('hos-buckhead', 'Buckhead General Hospital', 'hospital', 'Buckhead', '3100 Peachtree Rd NE', 'Atlanta', '30305', 33.844, -84.378, '(404) 555-0114', 1.85, { stars: 5, billers: ['Buckhead General Hospital', 'Peachtree Radiology Associates'] }),
  row('hos-clairmont', 'Clairmont Memorial Hospital', 'hospital', 'Druid Hills', '1550 Clairmont Rd', 'Decatur', '30033', 33.8, -84.315, '(404) 555-0115', 1.4, { stars: 3 }),
  row('hos-cobb', 'Cobb Valley Hospital', 'hospital', 'Marietta', '800 Church St NE', 'Marietta', '30060', 33.9625, -84.546, '(770) 555-0116', 1.3, { stars: 4 }),
  row('hos-flatshoals', 'Flat Shoals Medical Center', 'hospital', 'East Atlanta', '1290 Flat Shoals Ave SE', 'Atlanta', '30316', 33.7395, -84.342, '(404) 555-0117', 1.25, { stars: 3 }),
  row('hos-holcomb', 'Holcomb Bridge Hospital', 'hospital', 'Roswell', '1100 Holcomb Bridge Rd', 'Roswell', '30076', 34.004, -84.33, '(770) 555-0118', 1.6, { stars: 4 }),
  row('hos-ridgeline', 'Ridgeline Regional Hospital', 'hospital', 'Tucker', '4400 Lawrenceville Hwy', 'Tucker', '30084', 33.856, -84.218, '(770) 555-0119', 1.2, { stars: 2 }),
  row('hos-jonesboro', 'Jonesboro Road Medical Center', 'hospital', 'Morrow', '6500 Jonesboro Rd', 'Morrow', '30260', 33.584, -84.341, '(770) 555-0120', 1.15, { stars: 3 }),

  row('ort-howellmill', 'Howell Mill Orthopedics', 'ortho', 'Westside', '1180 Howell Mill Rd NW', 'Atlanta', '30318', 33.789, -84.412, '(404) 555-0122', 0.6),
  row('ort-perimeter', 'Perimeter Bone & Joint', 'ortho', 'Dunwoody', '1100 Hammond Dr NE', 'Atlanta', '30328', 33.9195, -84.3515, '(770) 555-0142', 0.55),

  row('cli-cabbagetown', 'Cabbagetown Family Health', 'clinic', 'Grant Park', '680 Memorial Dr SE', 'Atlanta', '30312', 33.747, -84.366, '(404) 555-0121', 0.95),
  row('cli-oakhurst', 'Oakhurst Community Clinic', 'clinic', 'Decatur', '650 E Lake Dr', 'Decatur', '30030', 33.759, -84.301, '(404) 555-0123', 0.78),
  row('cli-roswellrd', 'Roswell Road Medical Group', 'clinic', 'Sandy Springs', '6200 Roswell Rd', 'Sandy Springs', '30328', 33.93, -84.379, '(404) 555-0124', 1),
  row('cli-chamblee', 'Chamblee Family Practice', 'clinic', 'Chamblee', '5400 Peachtree Blvd', 'Chamblee', '30341', 33.888, -84.301, '(770) 555-0125', 0.88),
  row('cli-smyrna', 'Smyrna Village Clinic', 'clinic', 'Smyrna', '1250 Concord Rd SE', 'Smyrna', '30080', 33.859, -84.522, '(770) 555-0126', 0.82),
  row('cli-westend', 'West End Health Collective', 'clinic', 'West End', '870 Ralph David Abernathy Blvd SW', 'Atlanta', '30310', 33.7375, -84.417, '(404) 555-0127', 0.72),
  row('cli-stonemtn', 'Stone Mountain Family Care', 'clinic', 'Stone Mountain', '940 Main St', 'Stone Mountain', '30083', 33.807, -84.172, '(770) 555-0128', 0.85),
];

// The ZIP codes the sample can place someone in, with approximate centers.
export const ZIP_AREAS = [
  { zip: '30303', name: 'Downtown Atlanta', lat: 33.753, lon: -84.39 },
  { zip: '30306', name: 'Virginia-Highland', lat: 33.786, lon: -84.351 },
  { zip: '30308', name: 'Midtown', lat: 33.772, lon: -84.376 },
  { zip: '30309', name: 'Ansley Park', lat: 33.798, lon: -84.388 },
  { zip: '30305', name: 'Buckhead', lat: 33.832, lon: -84.385 },
  { zip: '30030', name: 'Decatur', lat: 33.771, lon: -84.297 },
  { zip: '30319', name: 'Brookhaven', lat: 33.869, lon: -84.335 },
  { zip: '30328', name: 'Sandy Springs', lat: 33.933, lon: -84.381 },
  { zip: '30339', name: 'Vinings', lat: 33.87, lon: -84.465 },
  { zip: '30060', name: 'Marietta', lat: 33.925, lon: -84.54 },
  { zip: '30080', name: 'Smyrna', lat: 33.864, lon: -84.515 },
  { zip: '30075', name: 'Roswell', lat: 34.045, lon: -84.365 },
  { zip: '30096', name: 'Duluth', lat: 33.983, lon: -84.15 },
  { zip: '30344', name: 'East Point', lat: 33.68, lon: -84.46 },
  { zip: '30316', name: 'East Atlanta', lat: 33.732, lon: -84.335 },
  { zip: '30084', name: 'Tucker', lat: 33.853, lon: -84.215 },
  { zip: '30114', name: 'Canton', lat: 34.237, lon: -84.491 },
  { zip: '30215', name: 'Fayetteville', lat: 33.449, lon: -84.455 },
];

export const SAMPLE_ADDRESSES = [
  '1043 N Highland Ave NE, Atlanta, GA 30306',
  '730 Peachtree St NE, Atlanta, GA 30308',
  '145 Clairemont Ave, Decatur, GA 30030',
  '3393 Peachtree Rd NE, Atlanta, GA 30305',
  '2450 Cumberland Pkwy SE, Atlanta, GA 30339',
  '55 Whitlock Ave NW, Marietta, GA 30060',
];
