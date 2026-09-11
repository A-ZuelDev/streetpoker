const chipFormatter = new Intl.NumberFormat('en-US');

export function formatChips(chips: number): string {
  return chipFormatter.format(chips);
}
