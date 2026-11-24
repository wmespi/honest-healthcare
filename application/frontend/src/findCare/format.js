// Dollars to the cent, without ".00" on a whole amount.
export const money = (amount) => `$${amount.toLocaleString('en-US', Number.isInteger(amount)
  ? {} : { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const miles = (distance) => `${distance.toFixed(1)} mi`;

// NPPES and Medicare spell names and addresses in capitals ("GRADY MEMORIAL
// HOSPITAL"), so the screen spells them as words. Text with lower-case letters
// is already spelled, and stays as it is.

// Capitals in any name or address: company endings, credentials and numerals.
const CAPITALS = [
  'LLC', 'PLLC', 'LLP', 'PC', 'PA', 'MD', 'DO', 'OD', 'DPM', 'DMD', 'DDS', 'NP', 'APRN', 'FNP',
  'LCSW', 'LPC', 'LMFT', 'MSW', 'II', 'III', 'IV',
];
// Street directions, the state and the country.
const PLACES = ['NE', 'NW', 'SE', 'SW', 'GA', 'US', 'USA'];

// A practice or hospital: the scans and the short names Georgia's practices go
// by. A word without a vowel is one of those too (SGMC, NSH), unless it's short
// for a word (WORDS), and "of", "and" and the like stay small after the first word.
const NAME = {
  capitals: new Set([...CAPITALS, ...PLACES, 'MRI', 'CT', 'PET', 'ENT', 'OB', 'GYN', 'OBGYN', 'GI', 'EEG',
    'ICU', 'ER', 'OT', 'VA', 'AU', 'AGA', 'AFC', 'AMC', 'ASC', 'ACS', 'ATL', 'ABC', 'DBA', 'HMA', 'MSO',
    'UHS', 'UMG', 'CHOA', 'ESOP', 'OBHG']),
  initials: true,
  small: true,
};
const WORDS = new Set(['ST', 'DR', 'DRS', 'JR', 'SR', 'MR', 'MRS', 'MT', 'FT', 'LTD', 'CTR', 'GRP', 'SVC', 'SVCS', 'MGMT', 'HLTH']);
const SMALL = new Set(['OF', 'AND', 'THE', 'FOR', 'IN', 'AT', 'TO', 'ON', 'BY', 'AN']);
// A street address or a city, where "CT" is a court and "SR" a state route.
const ADDRESS = { capitals: new Set([...CAPITALS, ...PLACES, 'PO', 'PMB', 'SR']) };
// A clinician.
const PERSON = { capitals: new Set(CAPITALS) };

// A word, or a part of one between hyphens and slashes. A capital starts it and
// follows a bracket or a comma, and an apostrophe or "Mc" when more letters
// follow: O'Neal and McQuaig, but Women's.
function part(text, rules) {
  const letters = text.replace(/[^A-Z]/g, '');
  if (letters === 'PHD') return text.replace('PHD', 'PhD');
  if (rules.capitals.has(letters)) return text;
  if (rules.initials && letters.length > 1 && !/[AEIOUY]/.test(letters) && !WORDS.has(letters)) return text;
  return text.toLowerCase()
    .replace(/(^|[(,])([a-z])/g, (_, before, letter) => before + letter.toUpperCase())
    .replace(/(['’]|^Mc)([a-z])(?=[a-z])/g, (_, before, letter) => before + letter.toUpperCase());
}

function word(text, first, rules) {
  if (text.length <= 1 || /[\d&]/.test(text)) return text;
  if (rules.small && !first && SMALL.has(text)) return text.toLowerCase();
  return text.split(/([-/])/).map((p) => (p === '-' || p === '/' ? p : part(p, rules))).join('');
}

const spell = (rules) => (text) => {
  if (!text || /[a-z]/.test(text)) return text;
  return text.split(' ').map((w, i) => word(w, i === 0, rules)).join(' ');
};

export const displayName = spell(NAME);
export const displayAddress = spell(ADDRESS);
export const displayPerson = spell(PERSON);
