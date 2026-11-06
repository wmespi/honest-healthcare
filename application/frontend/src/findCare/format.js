// Dollars to the cent, without ".00" on a whole amount.
export const money = (amount) => `$${amount.toLocaleString('en-US', Number.isInteger(amount)
  ? {} : { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const miles = (distance) => `${distance.toFixed(1)} mi`;

// Words that stay in capitals: company endings, credentials, numerals, street
// directions and scan names.
const CAPITALS = new Set([
  'LLC', 'PLLC', 'LLP', 'PC', 'PA', 'MD', 'DO', 'NP', 'II', 'III', 'IV',
  'NE', 'NW', 'SE', 'SW', 'MRI', 'CT', 'PET',
]);

const word = (w) => {
  if (w.length <= 1 || /\d/.test(w) || CAPITALS.has(w.replace(/[.,]/g, ''))) return w;
  return w.toLowerCase().replace(/(^|[-'’/(])([a-z])/g, (_, before, letter) => before + letter.toUpperCase());
};

// NPPES and Medicare spell names and addresses in capitals ("GRADY MEMORIAL
// HOSPITAL"), so the screen spells them as words. Text with lower-case letters
// is already spelled, and stays as it is.
export function displayName(text) {
  if (!text || /[a-z]/.test(text)) return text;
  return text.split(' ').map(word).join(' ');
}
