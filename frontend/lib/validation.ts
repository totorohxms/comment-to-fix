// Client-side input validation — mirrors backend/comments/service.py limits.
// The backend is the authority; this layer exists for immediate feedback.

export const COMMENT_MAX = 2000;

/** Returns an error message, or null when the text is valid. */
export function validateComment(text: string): string | null {
  const t = text.trim();
  if (!t) return "Comment cannot be empty.";
  if (t.length > COMMENT_MAX) return `Comment is too long (max ${COMMENT_MAX} characters).`;
  return null;
}
