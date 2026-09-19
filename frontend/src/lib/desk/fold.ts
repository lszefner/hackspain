/** Accent-insensitive text normalization; invoice data comes from the backend. */
export function fold(value: string) {
  return value.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}
