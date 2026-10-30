// A deliberately small Markdown subset. All user/model text goes through text nodes;
// raw HTML is never interpreted. No CDN or unsafe innerHTML is needed.
function appendInline(parent, text) {
  const pattern =
    /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(offset, match.index)));
    const token = match[0];
    let node;
    if (token.startsWith("`")) {
      node = document.createElement("code");
      node.textContent = token.slice(1, -1);
    } else if (token.startsWith("**")) {
      node = document.createElement("strong");
      node.textContent = token.slice(2, -2);
    } else {
      const split = token.indexOf("](");
      node = document.createElement("a");
      node.textContent = token.slice(1, split);
      node.href = token.slice(split + 2, -1);
      node.target = "_blank";
      node.rel = "noopener noreferrer";
    }
    parent.append(node);
    offset = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(offset)));
}

export function renderMarkdown(source, root) {
  const fragment = document.createDocumentFragment();
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  const flush = () => {
    if (paragraph.length) {
      const p = document.createElement("p");
      appendInline(p, paragraph.join(" "));
      fragment.append(p);
      paragraph = [];
    }
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {
      flush();
      const codeLines = [];
      while (++i < lines.length && !lines[i].trim().startsWith("```"))
        codeLines.push(lines[i]);
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = codeLines.join("\n");
      pre.append(code);
      fragment.append(pre);
    } else if (!line.trim()) {
      flush();
    } else if (/^\s*([-*_])\1\1+\s*$/.test(line)) {
      flush();
      fragment.append(document.createElement("hr"));
    } else if (/^#{1,6}\s/.test(line)) {
      flush();
      const match = line.match(/^(#{1,6})\s+(.+)$/);
      if (!match) continue;
      const heading = document.createElement(
        `h${Math.max(2, Math.min(match[1].length, 4))}`,
      );
      appendInline(heading, match[2]);
      fragment.append(heading);
    } else if (/^>\s?/.test(line)) {
      flush();
      const quote = document.createElement("blockquote");
      appendInline(quote, line.replace(/^>\s?/, ""));
      fragment.append(quote);
    } else if (/^\s*(?:[-*+] |\d+\. )/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const type = ordered ? "OL" : "UL";
      if (!list || list.tagName !== type || paragraph.length) {
        flush();
        list = document.createElement(type.toLowerCase());
        fragment.append(list);
      }
      const item = document.createElement("li");
      appendInline(item, line.replace(/^\s*(?:[-*+] |\d+\. )/, ""));
      list.append(item);
    } else {
      list = null;
      paragraph.push(line);
    }
  }
  flush();
  root.replaceChildren(fragment);
}
