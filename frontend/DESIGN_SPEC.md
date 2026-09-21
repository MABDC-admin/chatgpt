# Teacher AI Cloud Platform — Frontend Redesign Spec

## Design System
- **Stack:** Next.js 16 (App Router) + TypeScript + Tailwind CSS + shadcn/ui + lucide-react
- **Theme:** Light default, dark toggle (persisted to localStorage)
- **Accent:** Blue (#2563eb light / #3b82f6 dark)
- **Typography:** System sans-serif, base 16px, prose for message content
- **Spacing:** 4px grid, content max-w-3xl (chat/tools), max-w-5xl (admin)

## Layout Shell
- Fixed left sidebar (w-[260px], collapsible to 64px icon rail)
- Main content scrollable, centered
- Mobile: sidebar becomes overlay drawer below md breakpoint

## Pages

### /login
- Centered card, gradient background, no sidebar
- Email + password fields, full-width submit button
- Inline error alert (destructive variant)

### /chat
- Top bar: model selector, quality selector, cost hint badges, KB toggle switch
- Message rows: full-width, avatar left, no bubbles, alternating subtle bg
- Composer: fixed bottom, elevated, attachment button + auto-resize textarea + send/stop
- Attachments: removable chips above composer
- Sidebar: conversation list with search, hover dropdown menu (rename/archive/delete)

### /tools
- Two-column desktop: form left (tabs + fields), output right (scrollable card)
- Stacked on mobile
- Export buttons (DOCX/PDF/PPTX) as icon buttons top-right of output
- Cost estimate badge below submit

### /admin
- Summary stat cards grid
- Spend chart (inline SVG bar chart in card)
- User table (shadcn Table) with inline credit editing, role badges, action dropdowns
- Audit trail collapsible panel

### /images
- Responsive grid (2-4 columns), aspect-square thumbnails
- Hover overlay: prompt snippet, model, cost badge
- Click opens lightbox dialog

### /knowledge
- Upload drop zone (dashed border, cloud icon)
- Document list/table: filename, type badge, chunk count, status, date, delete

### /account
- Profile info (read-only email, name field, role badge)
- Change password section
- Credits summary card with progress bar

## Global Patterns
- Toasts (sonner) replace all alert() calls
- Skeleton placeholders for initial loads
- AlertDialog for destructive actions (replaces window.confirm)
- Empty states with icon + text + CTA
- Focus ring: ring-2 ring-blue-500 ring-offset-2
- Touch targets min 44x44px
