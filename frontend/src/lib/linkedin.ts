/** The longest post LinkedIn accepts. */
export const LINKEDIN_CHARS = 3000;

const NEW_POST = "https://www.linkedin.com/feed/?shareActive=true";
/** Longer than this and the address may be refused, so the new post opens empty and you paste. */
const LONGEST_URL = 6000;

/**
 * LinkedIn's own "start a post" window with the text already in it. Posting there is your click,
 * made on LinkedIn while signed in to LinkedIn: this site holds nothing that can post.
 *
 * The text is also copied to the clipboard before this opens, so a post too long to travel in an
 * address, or a LinkedIn that stops filling it in, only costs you a paste.
 */
export function composeUrl(text: string): string {
  const filled = `${NEW_POST}&text=${encodeURIComponent(text)}`;
  return filled.length <= LONGEST_URL ? filled : NEW_POST;
}
