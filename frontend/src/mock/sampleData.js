// Fictional practices and prices for the /mock/find-care experience. Nothing here
// comes from real data, and the names are made up so a sample price never sits next
// to a real business.

export const SAMPLE_PLAN = 'Blue Value HMO';

export const KIND_LABELS = {
  imaging: 'Imaging center',
  hospital: 'Hospital outpatient',
  clinic: 'Medical practice',
  lab: 'Lab',
  endoscopy: 'Endoscopy center',
  therapy: 'Physical therapy',
};

export const SERVICES = [
  { id: 'mri-lower-back', name: 'MRI of the lower back', group: 'Imaging', basePrice: 900, kinds: ['imaging', 'hospital'], keywords: 'lumbar spine scan' },
  { id: 'mri-knee', name: 'MRI of the knee', group: 'Imaging', basePrice: 850, kinds: ['imaging', 'hospital'], keywords: 'joint scan' },
  { id: 'mri-brain', name: 'MRI of the brain', group: 'Imaging', basePrice: 1100, kinds: ['imaging', 'hospital'], keywords: 'head scan' },
  { id: 'ct-abdomen', name: 'CT of the abdomen and pelvis', group: 'Imaging', basePrice: 1000, kinds: ['imaging', 'hospital'], keywords: 'cat scan stomach' },
  { id: 'chest-xray', name: 'Chest X-ray', group: 'Imaging', basePrice: 90, kinds: ['imaging', 'hospital', 'clinic'], keywords: 'xray lungs' },
  { id: 'abdominal-ultrasound', name: 'Abdominal ultrasound', group: 'Imaging', basePrice: 310, kinds: ['imaging', 'hospital', 'clinic'], keywords: 'sonogram stomach' },
  { id: 'mammogram', name: 'Screening mammogram', group: 'Imaging', basePrice: 190, kinds: ['imaging', 'hospital'], keywords: 'breast' },
  { id: 'colonoscopy', name: 'Colonoscopy', group: 'Procedures', basePrice: 1500, kinds: ['endoscopy', 'hospital'], keywords: 'screening gi' },
  { id: 'primary-care-visit', name: 'New patient primary care visit', group: 'Visits', basePrice: 170, kinds: ['clinic'], keywords: 'doctor pcp checkup' },
  { id: 'pt-evaluation', name: 'Physical therapy evaluation', group: 'Visits', basePrice: 130, kinds: ['therapy', 'hospital'], keywords: 'pt rehab' },
  { id: 'blood-count', name: 'Complete blood count', group: 'Labs', basePrice: 22, kinds: ['lab', 'clinic', 'hospital'], keywords: 'cbc blood work' },
  { id: 'cholesterol-panel', name: 'Cholesterol panel', group: 'Labs', basePrice: 34, kinds: ['lab', 'clinic', 'hospital'], keywords: 'lipid blood work' },
];

const HOURS_BY_KIND = {
  imaging: 'Mon to Sat, 7am to 7pm',
  hospital: 'Mon to Fri, 6am to 8pm',
  clinic: 'Mon to Fri, 8am to 5pm',
  lab: 'Mon to Sat, 6:30am to 4pm',
  endoscopy: 'Mon to Fri, 7am to 3pm',
  therapy: 'Mon to Fri, 7am to 6pm',
};

// priceLevel scales a service's basePrice, so hospitals sit well above imaging centers.
const row = (id, name, kind, neighborhood, street, city, zip, lat, lon, phone, priceLevel) => ({
  id, name, kind, neighborhood, address: `${street}, ${city}, GA ${zip}`, lat, lon, phone,
  hours: HOURS_BY_KIND[kind], priceLevel,
});

export const PROVIDERS = [
  row('img-dogwood', 'Dogwood Imaging', 'imaging', 'Decatur', '215 E Ponce de Leon Ave', 'Decatur', '30030', 33.7752, -84.2935, '(404) 555-0101', 0.42),
  row('img-clearpath', 'Clearpath Radiology', 'imaging', 'Midtown', '950 Spring St NW', 'Atlanta', '30309', 33.7815, -84.3892, '(404) 555-0102', 0.58),
  row('img-magnolia', 'Magnolia MRI & CT', 'imaging', 'Sandy Springs', '5670 Roswell Rd', 'Sandy Springs', '30328', 33.916, -84.3785, '(404) 555-0103', 0.5),
  row('img-beltline', 'Beltline Imaging', 'imaging', 'Inman Park', '310 Krog St NE', 'Atlanta', '30307', 33.7572, -84.364, '(404) 555-0104', 0.47),
  row('img-chattahoochee', 'Chattahoochee Diagnostic', 'imaging', 'Vinings', '3200 Paces Ferry Pl', 'Atlanta', '30339', 33.863, -84.469, '(770) 555-0105', 0.62),
  row('img-redclay', 'Red Clay Imaging', 'imaging', 'East Point', '2760 Main St', 'East Point', '30344', 33.6785, -84.4395, '(404) 555-0106', 0.38),
  row('img-lostmtn', 'Lost Mountain Imaging', 'imaging', 'Kennesaw', '1810 Cobb Pkwy NW', 'Kennesaw', '30152', 34.02, -84.612, '(770) 555-0107', 0.44),
  row('img-corners', 'Corners Imaging', 'imaging', 'Norcross', '5525 Jimmy Carter Blvd', 'Norcross', '30093', 33.923, -84.208, '(770) 555-0108', 0.4),
  row('img-ashford', 'Ashford Open MRI', 'imaging', 'Dunwoody', '4700 Ashford Dunwoody Rd', 'Dunwoody', '30338', 33.933, -84.337, '(770) 555-0109', 0.66),
  row('img-harbor', 'Harbor Oak Radiology', 'imaging', 'Smyrna', '2520 Atlanta Rd SE', 'Smyrna', '30080', 33.866, -84.512, '(770) 555-0110', 0.52),
  row('img-oldmilton', 'Old Milton Imaging', 'imaging', 'Alpharetta', '3400 Old Milton Pkwy', 'Alpharetta', '30005', 34.071, -84.281, '(770) 555-0111', 0.55),
  row('img-sugarloaf', 'Sugarloaf Scan Center', 'imaging', 'Duluth', '2100 Satellite Blvd', 'Duluth', '30097', 33.99, -84.133, '(770) 555-0112', 0.46),

  row('hos-ponce', 'Ponce Street Medical Center', 'hospital', 'Midtown', '550 Ponce de Leon Ave NE', 'Atlanta', '30308', 33.7725, -84.369, '(404) 555-0113', 1.55),
  row('hos-buckhead', 'Buckhead General Hospital', 'hospital', 'Buckhead', '3100 Peachtree Rd NE', 'Atlanta', '30305', 33.844, -84.378, '(404) 555-0114', 1.85),
  row('hos-clairmont', 'Clairmont Memorial Hospital', 'hospital', 'Druid Hills', '1550 Clairmont Rd', 'Decatur', '30033', 33.8, -84.315, '(404) 555-0115', 1.4),
  row('hos-cobb', 'Cobb Valley Hospital', 'hospital', 'Marietta', '800 Church St NE', 'Marietta', '30060', 33.9625, -84.546, '(770) 555-0116', 1.3),
  row('hos-flatshoals', 'Flat Shoals Medical Center', 'hospital', 'East Atlanta', '1290 Flat Shoals Ave SE', 'Atlanta', '30316', 33.7395, -84.342, '(404) 555-0117', 1.25),
  row('hos-holcomb', 'Holcomb Bridge Hospital', 'hospital', 'Roswell', '1100 Holcomb Bridge Rd', 'Roswell', '30076', 34.004, -84.33, '(770) 555-0118', 1.6),
  row('hos-ridgeline', 'Ridgeline Regional Hospital', 'hospital', 'Tucker', '4400 Lawrenceville Hwy', 'Tucker', '30084', 33.856, -84.218, '(770) 555-0119', 1.2),
  row('hos-jonesboro', 'Jonesboro Road Medical Center', 'hospital', 'Morrow', '6500 Jonesboro Rd', 'Morrow', '30260', 33.584, -84.341, '(770) 555-0120', 1.15),

  row('cli-cabbagetown', 'Cabbagetown Family Health', 'clinic', 'Grant Park', '680 Memorial Dr SE', 'Atlanta', '30312', 33.747, -84.366, '(404) 555-0121', 0.95),
  row('cli-howellmill', 'Howell Mill Health', 'clinic', 'Westside', '1180 Howell Mill Rd NW', 'Atlanta', '30318', 33.789, -84.412, '(404) 555-0122', 1.05),
  row('cli-oakhurst', 'Oakhurst Community Clinic', 'clinic', 'Decatur', '650 E Lake Dr', 'Decatur', '30030', 33.759, -84.301, '(404) 555-0123', 0.78),
  row('cli-roswellrd', 'Roswell Road Medical Group', 'clinic', 'Sandy Springs', '6200 Roswell Rd', 'Sandy Springs', '30328', 33.93, -84.379, '(404) 555-0124', 1),
  row('cli-chamblee', 'Chamblee Family Practice', 'clinic', 'Chamblee', '5400 Peachtree Blvd', 'Chamblee', '30341', 33.888, -84.301, '(770) 555-0125', 0.88),
  row('cli-smyrna', 'Smyrna Village Clinic', 'clinic', 'Smyrna', '1250 Concord Rd SE', 'Smyrna', '30080', 33.859, -84.522, '(770) 555-0126', 0.82),
  row('cli-westend', 'West End Health Collective', 'clinic', 'West End', '870 Ralph David Abernathy Blvd SW', 'Atlanta', '30310', 33.7375, -84.417, '(404) 555-0127', 0.72),
  row('cli-stonemtn', 'Stone Mountain Family Care', 'clinic', 'Stone Mountain', '940 Main St', 'Stone Mountain', '30083', 33.807, -84.172, '(770) 555-0128', 0.85),

  row('lab-fairlie', 'Fairlie Labs', 'lab', 'Downtown', '85 Fairlie St NW', 'Atlanta', '30303', 33.757, -84.39, '(404) 555-0129', 0.8),
  row('lab-briarcliff', 'Briarcliff Lab Services', 'lab', 'Druid Hills', '1330 Briarcliff Rd NE', 'Atlanta', '30306', 33.79, -84.335, '(404) 555-0130', 0.62),
  row('lab-cumberland', 'Cumberland Diagnostic Lab', 'lab', 'Vinings', '2900 Cumberland Pkwy SE', 'Atlanta', '30339', 33.876, -84.466, '(770) 555-0131', 0.7),
  row('lab-buford', 'Buford Highway Labs', 'lab', 'Doraville', '5100 Buford Hwy NE', 'Doraville', '30340', 33.895, -84.286, '(770) 555-0132', 0.55),

  row('end-midtown', 'Midtown Endoscopy Suites', 'endoscopy', 'Midtown', '1200 W Peachtree St NW', 'Atlanta', '30309', 33.788, -84.387, '(404) 555-0133', 0.8),
  row('end-sandy', 'Sandy Springs GI Center', 'endoscopy', 'Sandy Springs', '5900 Peachtree Dunwoody Rd', 'Sandy Springs', '30328', 33.918, -84.352, '(404) 555-0134', 0.7),
  row('end-decatur', 'Decatur Digestive Care', 'endoscopy', 'Decatur', '400 W Trinity Pl', 'Decatur', '30030', 33.774, -84.301, '(404) 555-0135', 0.62),
  row('end-marietta', 'Marietta Square Endoscopy', 'endoscopy', 'Marietta', '120 Church St NE', 'Marietta', '30060', 33.954, -84.549, '(770) 555-0136', 0.58),

  row('pt-beltline', 'Beltline Physical Therapy', 'therapy', 'Inman Park', '660 Irwin St NE', 'Atlanta', '30312', 33.759, -84.364, '(404) 555-0137', 0.95),
  row('pt-buckhead', 'Buckhead Motion PT', 'therapy', 'Buckhead', '3500 Lenox Rd NE', 'Atlanta', '30326', 33.847, -84.362, '(404) 555-0138', 1.1),
  row('pt-eastlake', 'East Lake Physical Therapy', 'therapy', 'East Lake', '2340 Hosea L Williams Dr NE', 'Atlanta', '30317', 33.7505, -84.305, '(404) 555-0139', 0.8),
  row('pt-cobb', 'Cobb Movement Therapy', 'therapy', 'Marietta', '1500 Powder Springs Rd SW', 'Marietta', '30064', 33.923, -84.564, '(770) 555-0140', 0.75),
];

// The ZIP codes the sample can place someone in, with approximate centers.
export const PLACES = [
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
];

export const SAMPLE_ADDRESSES = [
  '1043 N Highland Ave NE, Atlanta, GA 30306',
  '730 Peachtree St NE, Atlanta, GA 30308',
  '145 Clairemont Ave, Decatur, GA 30030',
  '3393 Peachtree Rd NE, Atlanta, GA 30305',
  '2450 Cumberland Pkwy SE, Atlanta, GA 30339',
  '55 Whitlock Ave NW, Marietta, GA 30060',
];
