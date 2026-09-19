/** Formato español: 1.234.567,89 € — los importes viajan en céntimos. */
export function eur(cent: number | null | undefined): string {
  if (cent === null || cent === undefined) return "—";
  const sign = cent < 0 ? "-" : "";
  const abs = Math.abs(cent);
  const entero = Math.floor(abs / 100)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const dec = (abs % 100).toString().padStart(2, "0");
  return `${sign}${entero},${dec} €`;
}

export function iban(v: string | null | undefined): string {
  if (!v) return "—";
  return v.replace(/(.{4})/g, "$1 ").trim();
}

export function fecha(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

export function hora(iso: string): string {
  return iso.slice(11, 19);
}

export function pct(n: number): string {
  return `${n.toString().replace(".", ",")} %`;
}

export function num(n: number): string {
  return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}
