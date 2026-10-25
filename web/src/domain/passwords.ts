const ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
export const MIN_PASSWORD = 10;

/** Случайный пароль без похожих символов (0/O, 1/l/I), чтобы его можно было продиктовать. */
export function generatePassword(length = 14, random: (n: number) => number[] = randomBytes): string {
  return random(length)
    .map((byte) => ALPHABET[byte % ALPHABET.length])
    .join("");
}

function randomBytes(n: number): number[] {
  return [...crypto.getRandomValues(new Uint8Array(n))];
}

/** Пустой пароль допустим (сгенерирует бэкенд); непустой — не короче MIN_PASSWORD. */
export function validateOptionalPassword(password: string): string | null {
  if (!password) return null;
  return password.length < MIN_PASSWORD ? `пароль не короче ${MIN_PASSWORD} символов` : null;
}
