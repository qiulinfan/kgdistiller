// Git may check text out with CRLF on Windows. Only line endings may differ:
// retain exact notice text, attribution, punctuation and spacing.
export function includesNotice(text, notice) {
  return text.replace(/\r\n/g, "\n").includes(notice.replace(/\r\n/g, "\n").trim());
}
