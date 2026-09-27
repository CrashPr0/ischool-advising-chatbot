// Safe subset of Markdown for bot replies.
//
// Escape the whole string first, then add only the tags this file creates.
// Model HTML such as <script> or <img onerror> stays text. Links are emitted
// only for http(s) URLs.

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function unescapeHtml(value) {
  return String(value)
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&gt;/g, ">")
    .replace(/&lt;/g, "<")
    .replace(/&amp;/g, "&");
}

function safeHttpUrl(raw) {
  const candidate = String(raw || "").trim();
  if (!candidate || /[\u0000-\u001F\u007F\s]/.test(candidate)) return null;

  let parsed;
  try {
    parsed = new URL(candidate);
  } catch (_error) {
    return null;
  }

  if (!['http:', 'https:'].includes(parsed.protocol)) {
    return null;
  }
  if (/[\u0000-\u001F\u007F\s]/.test(parsed.href)) return null;
  if (!/^https?:\/\//i.test(parsed.href)) return null;
  return escapeHtml(parsed.href);
}

function externalLink(href, innerHtml) {
  return (
    '<a class="message-link" href="' +
    href +
    '" target="_blank" rel="noopener noreferrer">' +
    innerHtml +
    "</a>"
  );
}

function renderEmphasis(value) {
  let text = String(value);
  text = text.replace(
    /\*\*([^\s*](?:[^*\n]*[^\s*])?)\*\*/g,
    (_, inner) => "<strong>" + inner + "</strong>"
  );
  text = text.replace(
    /__([^\s_](?:[^_\n]*[^\s_])?)__/g,
    (_, inner) => "<strong>" + inner + "</strong>"
  );
  text = text.replace(
    /(^|[^\w*])\*((?:[^\s*](?:[^*\n]*[^\s*])?))\*(?!\w)/g,
    (_, prefix, inner) => prefix + "<em>" + inner + "</em>"
  );
  text = text.replace(
    /(^|[^\w])_((?:[^\s_](?:[^_\n]*[^\s_])?))_(?!\w)/g,
    (_, prefix, inner) => prefix + "<em>" + inner + "</em>"
  );
  return text;
}

function renderInline(escaped) {
  const stash = [];
  const tokenFor = (html) => {
    const token = "\u0000" + stash.length + "\u0000";
    stash.push(html);
    return token;
  };
  const restore = (value) =>
    value.replace(/\u0000(\d+)\u0000/g, (_, index) => stash[Number(index)] || "");

  let text = String(escaped).replace(/`([^`\n]+)`/g, (_, code) =>
    tokenFor("<code>" + code + "</code>")
  );

  text = text.replace(/\[([^\]\n]+)\]\(([^)\n]+)\)/g, (match, label, url) => {
    const href = safeHttpUrl(unescapeHtml(url));
    if (!href) return match;
    const inner = restore(renderEmphasis(label));
    return tokenFor(externalLink(href, inner));
  });

  text = text.replace(/https?:\/\/[^\s<]+/gi, (rawUrl) => {
    const cleanUrl = rawUrl.replace(/[.,;:!?)}\]]+$/g, "");
    const href = safeHttpUrl(unescapeHtml(cleanUrl));
    if (!href) return rawUrl;
    const trailing = rawUrl.slice(cleanUrl.length);
    const visible = escapeHtml(unescapeHtml(cleanUrl));
    return tokenFor(externalLink(href, visible)) + trailing;
  });

  text = renderEmphasis(text);
  return restore(text);
}

const UL_LINE = /^[ \t]{0,3}(?:[-*]|•)[ \t]+(\S.*)$/;
const OL_LINE = /^[ \t]{0,3}\d{1,3}\.[ \t]+(\S.*)$/;
const HEADING_LINE = /^[ \t]{0,3}(#{1,3})[ \t]+(\S.*)$/;

function listKind(line) {
  if (UL_LINE.test(line)) return "ul";
  if (OL_LINE.test(line)) return "ol";
  return "";
}

function renderBotMarkdown(raw) {
  const normalized = String(raw ?? "")
    .replace(/\u0000/g, "")
    .replace(/\r\n?/g, "\n");
  const lines = escapeHtml(normalized).split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    if (lines[index].trim() === "") {
      index += 1;
      continue;
    }

    const heading = lines[index].match(HEADING_LINE);
    if (heading) {
      const level = heading[1].length;
      blocks.push(
        '<p class="md-heading md-h' +
          level +
          '"><strong>' +
          renderInline(heading[2]) +
          "</strong></p>"
      );
      index += 1;
      continue;
    }

    const kind = listKind(lines[index]);
    if (kind) {
      const pattern = kind === "ul" ? UL_LINE : OL_LINE;
      const items = [];
      while (index < lines.length) {
        if (lines[index].trim() === "") {
          const next = index + 1;
          if (next < lines.length && listKind(lines[next]) === kind) {
            index = next;
            continue;
          }
          break;
        }
        const item = lines[index].match(pattern);
        if (!item) break;
        items.push("<li>" + renderInline(item[1]) + "</li>");
        index += 1;
      }
      blocks.push("<" + kind + ">" + items.join("") + "</" + kind + ">");
      continue;
    }

    const paragraph = [];
    while (
      index < lines.length &&
      lines[index].trim() !== "" &&
      !HEADING_LINE.test(lines[index]) &&
      !listKind(lines[index])
    ) {
      paragraph.push(renderInline(lines[index]));
      index += 1;
    }
    blocks.push("<p>" + paragraph.join("<br>") + "</p>");
  }

  return blocks.join("");
}

function botMarkdownPlainText(raw) {
  try {
    const html = renderBotMarkdown(raw);
    const withBreaks = html
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/(p|li|ul|ol)>/gi, "\n")
      .replace(/<[^>]+>/g, "");
    return unescapeHtml(withBreaks).replace(/\n{3,}/g, "\n\n").trim();
  } catch (_error) {
    return String(raw ?? "");
  }
}
