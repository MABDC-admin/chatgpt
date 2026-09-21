"use client";

import { memo, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeHighlight from "rehype-highlight";
import type { Components } from "react-markdown";
import AuthedImage from "@/components/AuthedImage";

/**
 * Sanitisation policy for AI output.
 *
 * The model's response is untrusted text, not code. rehype-sanitize's default
 * schema blocks scripts and event handlers; on top of that this schema:
 *   - allows the GFM table tags the default drops,
 *   - allows the classes rehype-highlight and our column/icon layouts write,
 *   - restricts href to http(s), mailto and in-page anchors so no
 *     javascript: URL can ever reach the DOM.
 */
const schema = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames ?? []),
    "table", "thead", "tbody", "tr", "th", "td",
    "sup", "sub", "del", "kbd", "mark",
  ],
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code ?? []), ["className"]],
    span: [...(defaultSchema.attributes?.span ?? []), ["className"]],
    div:  [...(defaultSchema.attributes?.div  ?? []), ["className"]],
    a: [
      ["href"], ["title"], ["target", "_blank"], ["rel", "noopener", "noreferrer"],
    ],
    th: [["colSpan"], ["rowSpan"], ["align"]],
    td: [["colSpan"], ["rowSpan"], ["align"]],
    img: [["src"], ["alt"], ["title"]],
  },
  protocols: {
    ...defaultSchema.protocols,
    href: ["http", "https", "mailto", "#"],
  },
};

/**
 * Tiny library of inline SVG icons the model can drop into explanations.
 *
 * Written as `:icon:name:` in Markdown. Anything unknown falls back to the
 * literal text, so a mistyped shortcode is legible rather than blank.
 */
const ICONS: Record<string, string> = {
  sun:    '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.31 11.31 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.31-11.31 1.42-1.42"/>',
  leaf:   '<path d="M4 20c8 0 16-8 16-16-8 0-16 8-16 16Zm0 0c4-4 8-6 12-8"/>',
  water:  '<path d="M12 3s-6 7-6 12a6 6 0 0 0 12 0c0-5-6-12-6-12Z"/>',
  cloud:  '<path d="M17 18a4 4 0 0 0 0-8 6 6 0 0 0-11.7 1.4A3.5 3.5 0 0 0 6 18h11Z"/>',
  book:   '<path d="M4 5v14a2 2 0 0 0 2 2h12V3H6a2 2 0 0 0-2 2Zm2 0h11v14H6"/>',
  bulb:   '<path d="M9 18h6m-5 3h4M12 3a6 6 0 0 0-4 10c1 1 2 2 2 4h4c0-2 1-3 2-4a6 6 0 0 0-4-10Z"/>',
  check:  '<path d="m5 12 5 5L20 7"/>',
  cross:  '<path d="M6 6l12 12M18 6 6 18"/>',
  info:   '<circle cx="12" cy="12" r="9"/><path d="M12 8v.01M11 12h1v5h1"/>',
  warn:   '<path d="M12 3 2 20h20L12 3Zm0 6v5m0 3v.01"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
  spark:  '<path d="M12 3v6M12 15v6M3 12h6m6 0h6M6 6l4 4m4 4 4 4M6 18l4-4m4-4 4-4"/>',
  atom:   '<circle cx="12" cy="12" r="1.5"/><ellipse cx="12" cy="12" rx="10" ry="4"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(-60 12 12)"/>',
  globe:  '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
  ruler:  '<path d="M3 15h18v3H3zm3 0v-2m3 2v-3m3 3v-2m3 2v-3m3 3v-2"/>',
  flask:  '<path d="M9 3h6v5l5 10a2 2 0 0 1-2 3H6a2 2 0 0 1-2-3l5-10V3Zm-2 12h10"/>',
};

/**
 * Sugar the model can use for a two-column layout:
 *
 *   ::: columns
 *   ## Left
 *   ...
 *   ~~~
 *   ## Right
 *   ...
 *   :::
 *
 * The `~~~` divider splits the two panes. Anything unmatched leaves the raw
 * text alone. Kept as a string transform so it works even inside `<pre>`-free
 * plain markdown, without pulling in remark-directive.
 */
function expandColumns(source: string): string {
  // The model sometimes wraps `::: columns ... :::` in a triple-backtick fence
  // -- especially when told to "use this exact syntax", because the syntax
  // looks like a code sample to it. Unwrap those before matching, or the
  // block renders as literal text and the layout never fires.
  const unfenced = source.replace(
    /```(?:\w+)?\s*\n(::: columns\s*[\s\S]*?:::)\s*\n```/g,
    "$1",
  );
  return unfenced.replace(
    /(^|\n)::: columns\s*\n([\s\S]*?)\n:::(?=\n|$)/g,
    (_m, lead: string, body: string) => {
      const parts = body.split(/\n~{3,}\n/);
      if (parts.length !== 2) return `${lead}${body}`;
      const [left, right] = parts;
      return (
        `${lead}<div class="md-columns">` +
        `<div>\n\n${left.trim()}\n\n</div>` +
        `<div>\n\n${right.trim()}\n\n</div>` +
        `</div>\n`
      );
    },
  );
}

/** Replace `:icon:name:` shortcodes with inline SVG spans. */
function inlineIcons(source: string): string {
  return source.replace(/:icon:([a-z]{2,10}):/g, (match, name: string) => {
    const path = ICONS[name];
    if (!path) return match;
    return (
      `<span class="md-icon" aria-hidden="true">` +
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
      `stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${path}</svg>` +
      `</span>`
    );
  });
}

const components: Components = {
  // External links open in a new tab; anchor and mailto do not.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  a({ href, children, ...rest }: any) {
    const external = href && /^https?:/i.test(href);
    return (
      <a
        href={href}
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
        {...rest}
      >
        {children}
      </a>
    );
  },
  // Tables get a wrapper so wide comparison tables scroll horizontally
  // instead of blowing up the message column.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  table({ children }: any) {
    return (
      <div className="md-table-wrap">
        <table>{children}</table>
      </div>
    );
  },
  // Route generated-image URLs through AuthedImage so the fetch carries the
  // bearer token; other images (external references, if any survive the
  // sanitiser) render as an ordinary <img>.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  img({ src, alt }: any) {
    const url = typeof src === "string" ? src : "";
    if (url.startsWith("/api/images/") || url.startsWith("/media/images/")) {
      return (
        <span className="inline-image block my-3 max-w-[420px]">
          <AuthedImage src={url} alt={alt || "Image"} />
        </span>
      );
    }
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={url} alt={alt || ""} className="my-3 max-w-full rounded-lg" />;
  },
};

function MarkdownInner({ children }: { children: string }) {
  const source = useMemo(
    () => expandColumns(inlineIcons(children)),
    [children],
  );

  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          [rehypeSanitize, schema],
          [rehypeHighlight, { ignoreMissing: true, detect: true }],
        ]}
        components={components}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}

// Streaming updates re-render this on every token; memoising by source string
// prevents rebuilding the whole markdown tree per character.
export default memo(MarkdownInner, (prev, next) => prev.children === next.children);
